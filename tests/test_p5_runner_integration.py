from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import carla

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentPhase, SegmentContext
from src.experiment.exit_assistance import ExitContext
from src.experiment.timestamp import TimestampEnvelope
from src.logging.research_event import ResearchEvent
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_exit_runtime import ResearchExitInteractionObserver
from src.scenario.research_noa_persistence import (
    ResearchNoAObservationSource,
    build_segment,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.vehicle import VehicleState
from src.vehicle.carla_exit_route import SwitchableRouteLaneGeometrySource
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p4_research_noa_integration import IncrementingNanoseconds, make_config


def test_assisted_motion_is_gate_context_not_driver_intervention() -> None:
    state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )
    interaction = Mock()
    interaction.update.return_value = state
    coordinator = Mock()
    coordinator.lane_change_in_progress = True
    persistence = Mock()
    timestamp = persistence.source.get_host_timestamp.return_value
    observer = ResearchExitInteractionObserver(
        interaction,
        coordinator,
        Mock(),
        persistence,
    )

    driver_input = DriverInput()
    observer.update(driver_input)

    effective_input = interaction.update.call_args.args[0]
    assert effective_input.lane_change_in_progress is True
    coordinator.update.assert_called_once_with(
        driver_input, state, timestamp, timestamp
    )


def test_actual_runner_builds_p5_reference_and_persists_finish_before_close(
    tmp_path: Path,
) -> None:
    config = replace(
        make_config(tmp_path, "run-p5"),
        exit_assistance=ResearchExitAssistanceConfig(
            Path("config/town04_exit_routes_dev_v3.json"),
            "town04-exit-39",
            "module1-exit-1",
        ),
    )
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    world.map.name = "Carla/Maps/Town04"
    viewer = FakeDriverViewFactory()
    clock = IncrementingNanoseconds()
    validated_routes: list[str] = []
    runner = ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=viewer,
        monotonic_ns=clock,
        utc_ns=clock,
        exit_route_validator=lambda selected, world_map, hero: validated_routes.append(
            selected.route_id
        ),
    )

    with runner.session() as session:
        assert session.exit_assistance is not None
        assert session.exit_view_binding is not None
        assert viewer.created[0].exit_view_binding is session.exit_view_binding
        assert isinstance(
            session.bundle.control_lane_geometry,
            SwitchableRouteLaneGeometrySource,
        )
        session.run()

    assert validated_routes == ["town04-exit-39"]

    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    event_types = [row["event_type"] for row in rows]
    assert "lc_finished" in event_types
    assert event_types.index("lc_finished") < event_types.index("stage_end")


class TimelineLogger:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    def write_telemetry(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        state: VehicleState,
    ) -> None:
        self.timeline.append("write:telemetry")

    def write_event(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        event: ResearchEvent,
    ) -> None:
        self.timeline.append(f"write:{event.event_type}")


def test_actual_p5_runner_applies_control_before_delayed_event_io(
    tmp_path: Path,
) -> None:
    config = replace(
        make_config(tmp_path, "run-p5-order"),
        exit_assistance=ResearchExitAssistanceConfig(
            Path("config/town04_exit_routes_dev_v3.json"),
            "town04-exit-39",
            "module1-exit-order",
        ),
    )
    timeline: list[str] = []
    vehicle = FakeResearchVehicle()
    vehicle.events = timeline
    world = FakeResearchWorld(vehicle)
    world.map.name = "Carla/Maps/Town04"
    viewer = FakeDriverViewFactory()
    clock = IncrementingNanoseconds()
    inputs = iter(
        (
            DriverInput(
                activation_requested=True,
                steering=0.2,
                steering_engaged=True,
            ),
            DriverInput(brake=0.5, steering=0.2, steering_engaged=True),
        )
    )
    runner = ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=viewer,
        driver_input_source=lambda: next(inputs),
        monotonic_ns=clock,
        utc_ns=clock,
        exit_route_validator=lambda selected, world_map, hero: None,
    )
    session = runner._create_session(TimelineLogger(timeline))
    viewer.created[0].actions.extend(
        (
            timeline.clear,
            lambda: vehicle.apply_control(carla.VehicleControl(brake=0.5)),
        )
    )

    try:
        session.run()
    finally:
        session.close()

    control_index = next(
        index for index, value in enumerate(timeline) if value.startswith("control:")
    )
    event_index = next(
        index for index, value in enumerate(timeline) if value == "write:auto_request"
    )
    assert control_index < event_index
    brake_index = timeline.index("control:0.5")
    disengagement_index = timeline.index("write:disengagement")
    assert brake_index < disengagement_index


class SnapshotActor:
    id = 42

    def __init__(self, transform: carla.Transform) -> None:
        self.transform = transform

    def get_velocity(self) -> carla.Vector3D:
        return carla.Vector3D()

    def get_transform(self) -> carla.Transform:
        return self.transform


class SnapshotTimestamp:
    elapsed_seconds = 12.5


class Snapshot:
    frame = 321
    timestamp = SnapshotTimestamp()

    def __init__(self, actor: SnapshotActor) -> None:
        self.actor = actor

    def find(self, actor_id: int) -> SnapshotActor | None:
        return self.actor if actor_id == self.actor.id else None


class SnapshotWaypoint:
    road_id = 39
    section_id = 0
    lane_id = -3
    lane_width = 3.5
    transform = carla.Transform(carla.Location(x=10.0))

    def get_right_lane(self) -> None:
        return None


class SnapshotMap:
    def __init__(self) -> None:
        self.locations: list[carla.Location] = []

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> SnapshotWaypoint:
        self.locations.append(location)
        return SnapshotWaypoint()


class SnapshotWorld:
    def __init__(self, snapshot: Snapshot, carla_map: SnapshotMap) -> None:
        self.snapshot = snapshot
        self.carla_map = carla_map

    def get_snapshot(self) -> Snapshot:
        return self.snapshot

    def get_map(self) -> SnapshotMap:
        return self.carla_map


class LiveHero:
    id = 42

    def get_control(self) -> carla.VehicleControl:
        return carla.VehicleControl()

    def get_light_state(self) -> carla.VehicleLightState:
        return carla.VehicleLightState.NONE

    def get_transform(self) -> carla.Transform:
        raise AssertionError("live actor transform must not label a snapshot frame")


def test_p5_observation_uses_actor_transform_from_the_labeled_snapshot() -> None:
    snapshot_actor = SnapshotActor(carla.Transform(carla.Location(x=10.0, y=2.0)))
    carla_map = SnapshotMap()
    clock = IncrementingNanoseconds()
    source = ResearchNoAObservationSource(
        SnapshotWorld(Snapshot(snapshot_actor), carla_map),
        LiveHero(),
        monotonic_ns=clock,
        utc_ns=clock,
    )

    observation = source.get_observation()

    assert observation.timestamp.carla_snapshot is not None
    assert observation.timestamp.carla_snapshot.frame == 321
    assert source.last_snapshot_sample is not None
    assert source.last_snapshot_sample.transform is snapshot_actor.transform
    assert carla_map.locations == [snapshot_actor.transform.location]


def test_training_exit_uses_training_csv_phase_without_module_label(
    tmp_path: Path,
) -> None:
    persistence = make_config(tmp_path, "training-phase").persistence
    assert persistence is not None

    segment = build_segment(persistence, ExitContext.TRAINING)

    assert segment.phase is ExperimentPhase.TRAINING
    assert segment.module is None
    assert segment.condition is None
