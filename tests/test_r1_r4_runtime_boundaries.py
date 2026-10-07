from __future__ import annotations

import csv
from dataclasses import dataclass
from math import nan
from pathlib import Path

import carla
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction import (
    AutomationInteractionEvent,
    AutomationInteractionEventType,
    DriverInput,
)
from src.experiment.automation_interaction_types import DriverInputError
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchEvent
from src.scenario.research_noa_persistence import (
    PersistedInteractionObserver,
    ResearchNoAObservationSource,
    ResearchNoAPersistence,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.vehicle import VehicleObservation, VehicleState
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_automation_interaction import make_controller
from tests.test_noa_runtime import FakeVelocity
from tests.test_p4_research_noa_integration import (
    IncrementingNanoseconds,
    make_config,
    make_module_two_config,
)


class RecordingController:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self._state = AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.MANUAL,
        )

    @property
    def state(self) -> AutomationState:
        return self._state

    def update(self, driver_input: DriverInput) -> AutomationState:
        self.calls.append("interaction")
        return self._state

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        self.calls.append("initialize")
        return self._state

    @property
    def initialization_failure(self) -> None:
        return None

    def after_control_applied(self) -> None:
        self.calls.append("availability")


class RecordingPersistence:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def flush_automation_events(self) -> None:
        self.calls.append("event_flush")

    def capture(self) -> VehicleObservation:
        self.calls.append("telemetry")
        return VehicleObservation(
            TimestampEnvelope(HostClockTimestamp(1, 1)),
            VehicleState(0.0, 0.0, 0.0, 0.0, 0.0, None, "off"),
        )


class PrimaryInteractionError(RuntimeError):
    pass


class PersistenceCleanupError(RuntimeError):
    pass


class FailingController(RecordingController):
    def after_control_applied(self) -> None:
        raise PrimaryInteractionError


class FailingPersistence(RecordingPersistence):
    def flush_automation_events(self) -> None:
        raise PersistenceCleanupError


def test_post_control_hook_defers_nonessential_work_until_after_manual_apply() -> None:
    calls: list[str] = []
    observer = PersistedInteractionObserver(
        RecordingController(calls),
        RecordingPersistence(calls),
    )

    observer.update(DriverInput(brake=0.1))
    calls.append("manual_control")
    observer.after_control_applied()

    assert calls == [
        "interaction",
        "manual_control",
        "availability",
        "event_flush",
        "telemetry",
    ]


def test_post_control_cleanup_does_not_replace_primary_interaction_error() -> None:
    calls: list[str] = []
    observer = PersistedInteractionObserver(
        FailingController(calls),
        FailingPersistence(calls),
    )
    observer.update(DriverInput())

    with pytest.raises(PrimaryInteractionError):
        observer.after_control_applied()


def test_persisted_initialization_uses_the_same_controller_entrypoint() -> None:
    calls: list[str] = []
    observer = PersistedInteractionObserver(
        RecordingController(calls),
        RecordingPersistence(calls),
    )

    observer.initialize(
        ExperimentModule.MODULE_2,
        make_module_two_config(
            Path("unused"), "unused"
        ).automation_interaction.condition,
    )

    assert calls == ["initialize", "event_flush", "telemetry"]


def test_brake_disengagement_does_not_run_availability_gate_before_control() -> None:
    controller, _, events = make_controller()
    controller.update(DriverInput(activation_requested=True))
    event_count = len(events)

    controller.update(DriverInput(brake=0.05))

    assert all(
        event.event_type is not AutomationInteractionEventType.AVAILABILITY
        for event in events[event_count:]
    )


def test_initial_activation_records_request_before_gate_result() -> None:
    controller, _, events = make_controller()
    controller.geometry.context = None

    controller.initialize(
        ExperimentModule.MODULE_2,
        make_module_two_config(
            Path("unused"), "unused"
        ).automation_interaction.condition,
    )

    assert [event.event_type for event in events[:2]] == [
        AutomationInteractionEventType.REQUEST,
        AutomationInteractionEventType.AVAILABILITY,
    ]


@dataclass(frozen=True, slots=True)
class SnapshotActor:
    velocity: FakeVelocity
    transform: carla.Transform

    def get_velocity(self) -> FakeVelocity:
        return self.velocity

    def get_transform(self) -> carla.Transform:
        return self.transform


@dataclass(frozen=True, slots=True)
class SnapshotTimestamp:
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class Snapshot:
    frame: int
    timestamp: SnapshotTimestamp
    actor: SnapshotActor

    def find(self, actor_id: int) -> SnapshotActor | None:
        return self.actor if actor_id == 42 else None


class SnapshotWorld(FakeResearchWorld):
    def get_snapshot(self) -> Snapshot:
        return Snapshot(
            17,
            SnapshotTimestamp(2.0),
            SnapshotActor(FakeVelocity(3.0, 4.0, 0.0), carla.Transform()),
        )


def test_observation_speed_comes_from_snapshot_actor() -> None:
    vehicle = FakeResearchVehicle()
    vehicle.velocity = FakeVelocity(100.0, 0.0, 0.0)
    source = ResearchNoAObservationSource(
        SnapshotWorld(vehicle),
        vehicle,
        monotonic_ns=iter((10, 20, 30)).__next__,
        utc_ns=lambda: 40,
    )

    observation = source.get_observation()

    assert observation.state.speed_kmh == pytest.approx(18.0)
    assert observation.timestamp.host.monotonic_ns == 30
    assert observation.timestamp.carla_snapshot is not None
    assert observation.timestamp.carla_snapshot.frame == 17


class SequencedObservationSource:
    def __init__(self) -> None:
        self.host_times = iter((10, 20))

    def get_host_timestamp(self) -> TimestampEnvelope:
        value = next(self.host_times)
        return TimestampEnvelope(HostClockTimestamp(value, value))

    def get_observation(self) -> VehicleObservation:
        return VehicleObservation(
            TimestampEnvelope(HostClockTimestamp(30, 30)),
            VehicleState(0.0, 0.0, 0.0, 0.0, 0.0, None, "off"),
        )


class RecordingResearchLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def write_event(self, segment, timestamp, event: ResearchEvent) -> None:
        self.calls.append(("event", timestamp.host.monotonic_ns, event.event_type))

    def write_telemetry(self, segment, timestamp, state: VehicleState) -> None:
        self.calls.append(("telemetry", timestamp.host.monotonic_ns, ""))


def test_automation_events_keep_occurrence_stamps_and_drain_before_telemetry(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path).persistence
    assert config is not None
    logger = RecordingResearchLogger()
    persistence = ResearchNoAPersistence(
        config,
        logger,
        SequencedObservationSource(),
    )
    state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.MANUAL,
    )

    persistence.record_automation(
        AutomationInteractionEvent(AutomationInteractionEventType.REQUEST, state)
    )
    persistence.record_automation(
        AutomationInteractionEvent(AutomationInteractionEventType.TRANSITION, state)
    )
    assert logger.calls == []

    persistence.flush_automation_events()
    persistence.capture()

    assert logger.calls == [
        ("event", 10, "auto_request"),
        ("event", 20, "auto_state"),
        ("telemetry", 30, ""),
    ]


def test_rejected_module_two_start_never_enters_viewer_and_logs_failure(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_module_two_config(tmp_path, "run-rejected-start"),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with pytest.raises(RuntimeError, match="initial"), runner.session() as session:
        world.map.waypoint = None
        session.run()

    assert viewer_factory.created[0].control_modes_at_run == []
    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="") as events_file:
        events = list(csv.DictReader(events_file))
    assert any(
        row["event_type"] == "activation_failure"
        and "GEOMETRY_UNAVAILABLE" in row["event_value"]
        for row in events
    )
    assert [
        row["event_type"] for row in events if row["event_type"].startswith("stage_")
    ] == ["stage_start_attempt", "stage_start_failure"]
    assert vehicle.destroy_count == 1


def test_nan_driver_input_preserves_original_error_and_cleans_up_once(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_config(tmp_path, "run-nan-input"),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        driver_input_source=lambda: DriverInput(throttle=nan),
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with pytest.raises(DriverInputError) as caught, runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        session.run()

    assert caught.value.field == "throttle"
    assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
    assert viewer_factory.created[0].close_count == 1
    assert vehicle.destroy_count == 1


def test_actual_runner_records_persistence_delays_on_scheduler_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_config(tmp_path, "run-persistence-timing"),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
        performance_clock=clock,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.persistence is not None
        assert session.live_scheduler is not None
        original_waypoint = world.map.get_waypoint
        original_light_state = vehicle.get_light_state
        original_write_event = session.persistence.logger.write_event
        original_write_telemetry = session.persistence.logger.write_telemetry

        def delayed_waypoint(*args, **kwargs):
            clock.advance(0.1)
            return original_waypoint(*args, **kwargs)

        def delayed_light_state():
            clock.advance(0.3)
            return original_light_state()

        def delayed_write_event(*args, **kwargs):
            clock.advance(0.2)
            return original_write_event(*args, **kwargs)

        def delayed_write_telemetry(*args, **kwargs):
            clock.advance(0.2)
            return original_write_telemetry(*args, **kwargs)

        monkeypatch.setattr(world.map, "get_waypoint", delayed_waypoint)
        monkeypatch.setattr(vehicle, "get_light_state", delayed_light_state)
        monkeypatch.setattr(
            session.persistence.logger, "write_event", delayed_write_event
        )
        monkeypatch.setattr(
            session.persistence.logger,
            "write_telemetry",
            delayed_write_telemetry,
        )
        viewer_factory.created[0].actions = [lambda: None]

        session.run()

        diagnostics = session.live_scheduler.diagnostics
        assert diagnostics.operation_timing("map_getter").maximum_ms == pytest.approx(
            100.0
        )
        assert diagnostics.operation_timing("light_getter").maximum_ms == pytest.approx(
            300.0
        )
        assert diagnostics.operation_timing("event_write").maximum_ms == pytest.approx(
            200.0
        )
        assert diagnostics.operation_timing(
            "telemetry_write"
        ).maximum_ms == pytest.approx(200.0)
