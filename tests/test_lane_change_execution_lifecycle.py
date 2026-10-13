from __future__ import annotations

import json
from dataclasses import dataclass

import carla
import pygame
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.lane_change import LaneChangeDirection
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.scenario.research_lane_change import (
    LaneChangeCorrelation,
    ResearchLaneChangeInteractionObserver,
)
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.scenario.research_noa_traffic_manager import (
    LaneChangeSubmissionStatus,
    StaleLaneChangeGenerationError,
    TrafficManagerNoAControlBackend,
)
from src.scenario.turn_signal_audio import TurnSignalAudio
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter
from tests.research_noa_fakes import (
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
    FakeTrafficManagerRuntimeAttestor,
)


class Interaction:
    def __init__(self) -> None:
        self._state = AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.NOA_ACTIVE,
        )

    @property
    def state(self) -> AutomationState:
        return self._state

    @property
    def initialization_failure(self) -> ActivationFailureReason | None:
        return None

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        return self._state

    def update(self, driver_input: DriverInput) -> AutomationState:
        if driver_input.brake >= 0.05 or driver_input.steering_engaged:
            self._state = AutomationState(
                AutomationAvailability.AVAILABLE,
                DrivingControlMode.MANUAL,
            )
        return self._state

    def after_control_applied(self) -> None:
        return


class Vehicle(FakeResearchVehicle):
    def set_light_state(self, state: carla.VehicleLightState) -> None:
        self.current_light_state = state


class Source:
    def __init__(self, baseline: ResearchSnapshotSample) -> None:
        self.last_snapshot_sample: ResearchSnapshotSample | None = baseline
        self.host_ns = baseline.timestamp.host.monotonic_ns

    def get_host_timestamp(self) -> TimestampEnvelope:
        self.host_ns += 1
        return TimestampEnvelope(HostClockTimestamp(self.host_ns, self.host_ns + 100))


class Persistence:
    def __init__(self, baseline: ResearchSnapshotSample) -> None:
        self.source = Source(baseline)
        self.events: list[tuple[str, dict[str, object]]] = []

    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None:
        self.events.append((event_type, json.loads(payload_json)))


@dataclass(slots=True)
class Rig:
    vehicle: Vehicle
    traffic_manager: FakeTrafficManager
    backend: TrafficManagerNoAControlBackend
    persistence: Persistence
    observer: ResearchLaneChangeInteractionObserver


def timestamp(frame: int | None) -> TimestampEnvelope:
    snapshot = (
        None
        if frame is None
        else CarlaSnapshotTimestamp(
            float(frame), frame, 1_000 + frame * 10 - 2, 1_000 + frame * 10 - 1
        )
    )
    return TimestampEnvelope(
        HostClockTimestamp(1_000 + (0 if frame is None else frame * 10), 2_000),
        snapshot,
    )


def sample(
    frame: int | None,
    *,
    y: float,
    lane_id: int,
    x: float = 10.0,
    yaw: float = 0.0,
    road_id: int = 36,
    section_id: int = 0,
    lane_transform: carla.Transform | None = None,
    left_lane_id: int | None = None,
    left_transform: carla.Transform | None = None,
    left_road_id: int | None = None,
    left_section_id: int | None = None,
    right_lane_id: int | None = None,
    right_transform: carla.Transform | None = None,
    right_road_id: int | None = None,
    right_section_id: int | None = None,
) -> ResearchSnapshotSample:
    return ResearchSnapshotSample(
        timestamp(frame),
        carla.Transform(carla.Location(x=x, y=y), carla.Rotation(yaw=yaw)),
        road_id,
        section_id,
        lane_id,
        3.5,
        lane_transform,
        right_lane_id,
        3.5 if right_lane_id is not None else None,
        right_transform,
        carla.VehicleControl(),
        "right",
        70.0,
        left_lane_id,
        3.5 if left_lane_id is not None else None,
        left_transform,
        None if left_lane_id is None else (
            left_road_id if left_road_id is not None else road_id
        ),
        None if left_lane_id is None else (
            left_section_id if left_section_id is not None else section_id
        ),
        None if right_lane_id is None else (
            right_road_id if right_road_id is not None else road_id
        ),
        None if right_lane_id is None else (
            right_section_id if right_section_id is not None else section_id
        ),
    )


def make_rig(
    direction: LaneChangeDirection,
    *,
    target_offset_override: float | None = None,
    submit_general: bool = True,
    baseline_yaw: float = 0.0,
    source_road_id: int = 36,
    source_section_id: int = 0,
    target_road_id: int = 36,
) -> Rig:
    vehicle = Vehicle()
    selected = (
        carla.VehicleLightState.LeftBlinker
        if direction is LaneChangeDirection.LEFT
        else carla.VehicleLightState.RightBlinker
    )
    vehicle.current_light_state = carla.VehicleLightState.LowBeam | selected
    source_y = 0.0
    left_y = -3.5 if target_offset_override is None else target_offset_override
    right_y = 3.5 if target_offset_override is None else target_offset_override
    target_id = -2 if direction is LaneChangeDirection.LEFT else -4
    baseline = sample(
        10,
        y=source_y,
        yaw=baseline_yaw,
        road_id=source_road_id,
        section_id=source_section_id,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=source_y)),
        left_lane_id=-2,
        left_transform=carla.Transform(carla.Location(x=10.0, y=left_y)),
        left_road_id=target_road_id,
        left_section_id=source_section_id,
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=right_y)),
        right_road_id=target_road_id,
        right_section_id=source_section_id,
    )
    persistence = Persistence(baseline)
    world = FakeResearchWorld(vehicle)
    traffic_manager = FakeTrafficManager(8123)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        traffic_manager,
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()
    observer = ResearchLaneChangeInteractionObserver(
        Interaction(),
        CarlaAdjacentLaneAdapter(world.get_map()),
        vehicle,
        persistence,
        LaneChangeCorrelation("GENERAL", "route-event", 0.15),
        executor=backend,
        completion_config=AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
    )
    if submit_general:
        observer.update(DriverInput(lane_change_direction=direction))
    assert target_id in (-2, -4)
    return Rig(vehicle, traffic_manager, backend, persistence, observer)


def drive_to_completion(rig: Rig, direction: LaneChangeDirection) -> None:
    sign = -1.0 if direction is LaneChangeDirection.LEFT else 1.0
    target_id = -2 if direction is LaneChangeDirection.LEFT else -4
    source_id = -3
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=1.8 * sign,
        lane_id=source_id,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        left_lane_id=-2,
        left_transform=carla.Transform(carla.Location(x=10.0, y=-3.5)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )
    rig.observer.after_control_applied()
    rig.persistence.source.last_snapshot_sample = sample(
        12,
        y=2.8 * sign,
        lane_id=target_id,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=3.5 * sign)),
        left_lane_id=source_id if direction is LaneChangeDirection.RIGHT else None,
        left_transform=carla.Transform(carla.Location(x=10.0, y=0.0))
        if direction is LaneChangeDirection.RIGHT
        else None,
        right_lane_id=source_id if direction is LaneChangeDirection.LEFT else None,
        right_transform=carla.Transform(carla.Location(x=10.0, y=0.0))
        if direction is LaneChangeDirection.LEFT
        else None,
    )
    rig.observer.after_control_applied()
    assert rig.backend.lane_change_in_flight is not None
    rig.persistence.source.last_snapshot_sample = sample(
        13,
        y=3.5 * sign,
        lane_id=target_id,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=3.5 * sign)),
        left_lane_id=source_id if direction is LaneChangeDirection.RIGHT else None,
        left_transform=carla.Transform(carla.Location(x=10.0, y=0.0))
        if direction is LaneChangeDirection.RIGHT
        else None,
        right_lane_id=source_id if direction is LaneChangeDirection.LEFT else None,
        right_transform=carla.Transform(carla.Location(x=10.0, y=0.0))
        if direction is LaneChangeDirection.LEFT
        else None,
    )
    rig.observer.after_control_applied()


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_snapshot_motion_alignment_completes_and_clears_only_selected_signal(
    monkeypatch: pytest.MonkeyPatch,
    direction: LaneChangeDirection,
) -> None:
    rig = make_rig(direction)
    selected = (
        carla.VehicleLightState.LeftBlinker
        if direction is LaneChangeDirection.LEFT
        else carla.VehicleLightState.RightBlinker
    )
    sound = pytest.importorskip("tests.test_turn_signal_audio").FakeSound()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (22_050, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Sound", lambda *, buffer: sound)
    audio = TurnSignalAudio()
    audio.update(active=True, now=0.0)

    drive_to_completion(rig, direction)
    audio.update(
        active=bool(
            rig.vehicle.get_light_state()
            & (carla.VehicleLightState.LeftBlinker | carla.VehicleLightState.RightBlinker)
        ),
        now=0.1,
    )

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases == ["REQUESTED", "SUBMITTED_TO_TM", "IN_PROGRESS", "COMPLETED"]
    request = next(
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_request"
    )
    assert request["context"] == "GENERAL"
    assert request["lc_event_id"] is None
    assert request["control_mode"] == "NOA_ACTIVE"
    completed = next(
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle" and payload["lifecycle"] == "COMPLETED"
    )
    assert completed["generation"] == rig.backend.activation_generation
    assert completed["observed_lane_id"] == (-2 if direction is LaneChangeDirection.LEFT else -4)
    assert completed["boundary_outside_frame"] == 10
    assert completed["boundary_inside_frame"] == 11
    assert rig.backend.lane_change_in_flight is None
    assert not rig.vehicle.get_light_state() & selected
    assert rig.vehicle.get_light_state() & carla.VehicleLightState.LowBeam
    assert sound.stop_count == 1


def test_confirmed_p5_uses_shared_submission_and_completion_lifecycle() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)

    rig.observer.submit_confirmed_p5()
    drive_to_completion(rig, LaneChangeDirection.RIGHT)

    request = next(
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_request"
    )
    lifecycle = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert request["context"] == "P5"
    assert request["lc_event_id"] == "route-event"
    assert [payload["lifecycle"] for payload in lifecycle] == [
        "REQUESTED",
        "SUBMITTED_TO_TM",
        "IN_PROGRESS",
        "COMPLETED",
    ]
    assert all(payload["context"] == "P5" for payload in lifecycle)
    assert all(payload["lc_event_id"] == "route-event" for payload in lifecycle)


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_completion_preserves_opposite_blinker(direction: LaneChangeDirection) -> None:
    rig = make_rig(direction)
    opposite = (
        carla.VehicleLightState.RightBlinker
        if direction is LaneChangeDirection.LEFT
        else carla.VehicleLightState.LeftBlinker
    )
    rig.vehicle.current_light_state |= opposite

    drive_to_completion(rig, direction)

    assert rig.vehicle.get_light_state() & opposite


def test_completed_geometry_survives_unverified_signal_setter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)

    def fail_setter(state: carla.VehicleLightState) -> None:
        raise RuntimeError("light command acknowledgement lost")

    monkeypatch.setattr(rig.vehicle, "set_light_state", fail_setter)

    drive_to_completion(rig, LaneChangeDirection.RIGHT)

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    autooff = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_signal_autooff"
    ]
    assert phases[-1] == "COMPLETED"
    assert autooff[-1]["setter_result"].startswith("UNVERIFIED:RuntimeError")
    assert autooff[-1]["selected_cleared"] is False


@pytest.mark.parametrize(
    "bad_sample",
    [
        sample(None, y=3.5, lane_id=-4, lane_transform=carla.Transform()),
        sample(9, y=3.5, lane_id=-4, lane_transform=carla.Transform()),
        sample(11, y=3.5, lane_id=-4, road_id=37, lane_transform=carla.Transform()),
        sample(11, y=3.5, lane_id=-4, lane_transform=None),
    ],
    ids=["missing-frame", "regressed-frame", "wrong-road", "missing-geometry"],
)
def test_invalid_completion_evidence_is_unverified_and_keeps_signal(
    bad_sample: ResearchSnapshotSample,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = bad_sample

    rig.observer.after_control_applied()

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases[-1] == "UNVERIFIED"
    assert rig.backend.lane_change_in_flight is not None
    assert rig.vehicle.get_light_state() & carla.VehicleLightState.RightBlinker


def test_zero_source_target_separation_blocks_submission() -> None:
    rig = make_rig(
        LaneChangeDirection.RIGHT,
        target_offset_override=0.0,
    )

    phases = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases[-1]["lifecycle"] == "UNVERIFIED"
    assert phases[-1]["reason"] == "SOURCE_TARGET_GEOMETRY_NOT_SEPARATED"
    assert rig.backend.lane_change_in_flight is None
    assert all(call[0] != "force_lane_change" for call in rig.traffic_manager.calls)


def test_wrong_side_target_geometry_is_unverified() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=-3.5,
        lane_id=-4,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=-3.5)),
        left_lane_id=-3,
        left_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
    )

    rig.observer.after_control_applied()

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases[-1] == "UNVERIFIED"
    assert rig.backend.lane_change_in_flight is not None


def test_extra_request_is_rejected_ongoing_without_interrupting_original() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    original = rig.backend.lane_change_in_flight

    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))

    requests = [payload for event, payload in rig.persistence.events if event == "lane_change_request"]
    assert requests[-1]["disposition"] == "REJECTED_ONGOING"
    assert rig.backend.lane_change_in_flight == original
    assert len(
        [call for call in rig.traffic_manager.calls if call[0] == "force_lane_change"]
    ) == 1


def test_duplicate_frame_is_ignored_and_frame_gap_is_recorded() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    between = sample(
        11,
        y=1.8,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )
    rig.persistence.source.last_snapshot_sample = between
    rig.observer.after_control_applied()
    event_count = len(rig.persistence.events)

    rig.observer.after_control_applied()

    assert len(rig.persistence.events) == event_count
    rig.persistence.source.last_snapshot_sample = sample(
        15,
        y=3.5,
        lane_id=-4,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
        left_lane_id=-3,
        left_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
    )
    rig.observer.after_control_applied()
    geometric = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_geometric_sample"
    ]
    assert geometric[-1]["frame_gap"] == 4
    assert geometric[-1]["status"] == "COMPLETED"


def test_manual_state_without_confirmed_deregistration_retains_flight_and_signal() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    generation = rig.backend.activation_generation
    lights = rig.vehicle.get_light_state()

    rig.observer.update(DriverInput(brake=0.1))

    assert rig.backend.activation_generation == generation
    assert rig.backend.lane_change_in_flight is not None
    assert rig.vehicle.get_light_state() == lights
    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases[-1] == "UNVERIFIED"
    assert rig.observer.lane_change_in_progress is True


def test_activation_and_lane_request_same_cycle_does_not_force() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.backend.complete_lane_change(rig.backend.activation_generation)
    initial_forces = len(
        [call for call in rig.traffic_manager.calls if call[0] == "force_lane_change"]
    )
    rig.observer.finish_lane_change_observation("RESET", timestamp(11))

    rig.observer.update(
        DriverInput(
            activation_requested=True,
            lane_change_direction=LaneChangeDirection.LEFT,
        )
    )

    requests = [payload for event, payload in rig.persistence.events if event == "lane_change_request"]
    assert requests[-1]["disposition"] == "REJECTED_ACTIVATION_CYCLE"
    assert len(
        [call for call in rig.traffic_manager.calls if call[0] == "force_lane_change"]
    ) == initial_forces


def test_manual_override_reactivation_rejects_stale_request_token() -> None:
    vehicle = Vehicle()
    world = FakeResearchWorld(vehicle)
    traffic_manager = FakeTrafficManager(8123)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        traffic_manager,
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()
    stale_token = backend.activation_generation
    backend.enter_manual_control()
    backend.enter_noa_control()

    with pytest.raises(StaleLaneChangeGenerationError):
        backend.submit_lane_change(
            "stale-request",
            activation_generation=stale_token,
            right_lane_change=True,
        )

    assert all(call[0] != "force_lane_change" for call in traffic_manager.calls)


def test_force_exception_is_unverified_and_locks_pending_request() -> None:
    class FailingTrafficManager(FakeTrafficManager):
        def force_lane_change(
            self,
            actor: FakeResearchVehicle,
            direction: bool,
        ) -> None:
            super().force_lane_change(actor, direction)
            raise RuntimeError("delivery acknowledgement lost")

    vehicle = Vehicle()
    world = FakeResearchWorld(vehicle)
    manager = FailingTrafficManager(8123)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        manager,
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()
    token = backend.activation_generation

    result = backend.submit_lane_change(
        "uncertain",
        activation_generation=token,
        right_lane_change=True,
    )

    assert result.status is LaneChangeSubmissionStatus.UNVERIFIED
    assert backend.lane_change_in_flight == result
    blocked = backend.submit_lane_change(
        "second",
        activation_generation=token,
        right_lane_change=False,
    )
    assert blocked.status is LaneChangeSubmissionStatus.REJECTED


def test_heading_and_longitudinal_change_do_not_start_lateral_progress() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=12.0,
        y=0.0,
        yaw=30.0,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=12.0, y=0.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=12.0, y=3.5)),
    )

    rig.observer.after_control_applied()

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases == ["REQUESTED", "SUBMITTED_TO_TM"]
    assert not any(event == "lane_change_signal_autooff" for event, _ in rig.persistence.events)


def test_misaligned_source_baseline_blocks_force() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, baseline_yaw=30.0)

    lifecycle = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert lifecycle[-1]["reason"] == "SOURCE_BASELINE_MISALIGNED"
    assert all(call[0] != "force_lane_change" for call in rig.traffic_manager.calls)


def test_neighbor_identity_on_different_road_blocks_force() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, target_road_id=37)

    lifecycle = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert lifecycle[-1]["reason"] == "TARGET_BASELINE_IDENTITY_MISMATCH"
    assert all(call[0] != "force_lane_change" for call in rig.traffic_manager.calls)


def test_neighbor_identity_change_after_submission_is_unverified() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=1.8,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        right_lane_id=-4,
        right_road_id=37,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )

    rig.observer.after_control_applied()

    lifecycle = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert lifecycle[-1]["lifecycle"] == "UNVERIFIED"
    assert lifecycle[-1]["reason"] == "GEOMETRY_CONTINUITY_UNAVAILABLE"


def test_metadata_only_lane_transition_does_not_complete() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=12.0,
        y=0.0,
        yaw=30.0,
        lane_id=-4,
        lane_transform=carla.Transform(
            carla.Location(x=12.0, y=0.0),
            carla.Rotation(yaw=30.0),
        ),
        left_lane_id=-3,
        left_transform=carla.Transform(carla.Location(x=12.0, y=-3.5)),
    )

    rig.observer.after_control_applied()

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert "IN_PROGRESS" not in phases
    assert "COMPLETED" not in phases
    assert not any(event == "lane_change_signal_autooff" for event, _ in rig.persistence.events)


def test_pending_p5_samples_cannot_progress_before_submission() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    rig.observer.update(
        DriverInput(
            lane_change_direction=LaneChangeDirection.RIGHT,
            exit_confirm_requested=True,
        )
    )
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=1.8,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )

    rig.observer.after_control_applied()

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert "IN_PROGRESS" not in phases
    assert "COMPLETED" not in phases


def test_missing_snapshot_latches_unverified_evidence() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = None
    rig.observer.after_control_applied()

    drive_to_completion(rig, LaneChangeDirection.RIGHT)

    phases = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases[-1] == "UNVERIFIED"
    assert "COMPLETED" not in phases
    assert rig.backend.lane_change_in_flight is not None


def test_nonfinite_observation_is_unverified_without_autooff() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=float("nan"),
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )

    rig.observer.after_control_applied()

    phases = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert phases[-1]["lifecycle"] == "UNVERIFIED"
    assert phases[-1]["reason"] == "GEOMETRY_NONFINITE"
    assert not any(event == "lane_change_signal_autooff" for event, _ in rig.persistence.events)


def test_light_getter_failure_does_not_repeat_physical_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    original_getter = rig.vehicle.get_light_state
    calls = 0

    def fail_completion_getter() -> carla.VehicleLightState:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("light read unavailable")
        return original_getter()

    monkeypatch.setattr(rig.vehicle, "get_light_state", fail_completion_getter)

    drive_to_completion(rig, LaneChangeDirection.RIGHT)
    rig.observer.after_control_applied()

    completed = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle" and payload["lifecycle"] == "COMPLETED"
    ]
    autooff = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_signal_autooff"
    ]
    assert len(completed) == 1
    assert len(autooff) == 1
    assert autooff[0]["before_result"].startswith("UNVERIFIED:RuntimeError")
    assert autooff[0]["before"] is None
    assert autooff[0]["readback_host_monotonic_ns"] is None


def test_light_readback_failure_records_no_fabricated_value_or_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    original_getter = rig.vehicle.get_light_state
    calls = 0

    def fail_readback_getter() -> carla.VehicleLightState:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("light readback unavailable")
        return original_getter()

    monkeypatch.setattr(rig.vehicle, "get_light_state", fail_readback_getter)

    drive_to_completion(rig, LaneChangeDirection.RIGHT)

    autooff = next(
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_signal_autooff"
    )
    assert autooff["readback_result"].startswith("UNVERIFIED:RuntimeError")
    assert autooff["readback"] is None
    assert autooff["readback_host_monotonic_ns"] is None
    assert autooff["selected_cleared"] is None


def test_adjacent_lane_runtime_error_is_rejected_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)

    def fail_evaluation(vehicle: Vehicle, direction: LaneChangeDirection) -> None:
        raise RuntimeError("CARLA waypoint RPC unavailable")

    monkeypatch.setattr(
        rig.observer._requests._adjacent_lanes,
        "evaluate",
        fail_evaluation,
    )

    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))

    request = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_request"
    ][-1]
    assert request["disposition"] == "REJECTED_RESOURCE_UNAVAILABLE"
    assert request["static_eligibility_reason"] == "RESOURCE_UNAVAILABLE"
    assert all(call[0] != "force_lane_change" for call in rig.traffic_manager.calls)
