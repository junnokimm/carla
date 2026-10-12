from __future__ import annotations

import json
from dataclasses import dataclass

import carla
import pygame

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    DriverInput,
)
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.lane_change import LaneChangeDirection
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.scenario.research_lane_change import (
    LaneChangeCorrelation,
    ResearchLaneChangeInteractionObserver,
)
from src.scenario.research_lane_change_input import LaneChangeRequestInput
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter
from tests.test_noa_runtime import FakeVehicle, make_map


@dataclass(frozen=True, slots=True)
class KeyEvent:
    type: int
    key: int
    dict: dict[str, int]


def key_down(key: int, *, repeat: bool = False) -> KeyEvent:
    return KeyEvent(pygame.KEYDOWN, key, {"repeat": int(repeat)})


def key_up(key: int) -> KeyEvent:
    return KeyEvent(pygame.KEYUP, key, {})


def test_indicator_off_to_on_emits_each_direction_without_p5() -> None:
    subject = LaneChangeRequestInput()

    left = subject.direction([key_down(pygame.K_z)], carla.VehicleLightState.NONE)
    subject.direction([key_up(pygame.K_z)], carla.VehicleLightState.LeftBlinker)
    right = subject.direction(
        [key_down(pygame.K_x)], carla.VehicleLightState.LeftBlinker
    )

    assert left is LaneChangeDirection.LEFT
    assert right is LaneChangeDirection.RIGHT


def test_repeat_held_and_toggle_off_do_not_emit_requests() -> None:
    subject = LaneChangeRequestInput()

    first = subject.direction([key_down(pygame.K_z)], carla.VehicleLightState.NONE)
    held = subject.direction(
        [key_down(pygame.K_z)], carla.VehicleLightState.LeftBlinker
    )
    repeated = subject.direction(
        [key_down(pygame.K_z, repeat=True)], carla.VehicleLightState.LeftBlinker
    )
    subject.direction([key_up(pygame.K_z)], carla.VehicleLightState.LeftBlinker)
    toggle_off = subject.direction(
        [key_down(pygame.K_z)], carla.VehicleLightState.LeftBlinker
    )

    assert first is LaneChangeDirection.LEFT
    assert held is None
    assert repeated is None
    assert toggle_off is None


def test_same_batch_toggles_coalesce_to_final_actual_indicator() -> None:
    subject = LaneChangeRequestInput()

    direction = subject.direction(
        [key_down(pygame.K_z), key_down(pygame.K_x)],
        carla.VehicleLightState.NONE,
    )

    assert direction is LaneChangeDirection.RIGHT


def test_adjacent_lane_static_eligibility_keeps_gap_unverified_by_design() -> None:
    vehicle = FakeVehicle()
    carla_map = make_map(vehicle)
    adapter = CarlaAdjacentLaneAdapter(carla_map)

    eligible = adapter.evaluate(vehicle, LaneChangeDirection.RIGHT)
    carla_map.waypoint.right = None
    missing = adapter.evaluate(vehicle, LaneChangeDirection.RIGHT)

    assert eligible.eligible is True
    assert eligible.reason == "STATIC_ELIGIBLE"
    assert eligible.target_lane_id == -4
    assert missing.eligible is False
    assert missing.reason == "ADJACENT_LANE_MISSING"


class Interaction:
    def __init__(self, mode: DrivingControlMode) -> None:
        self._state = AutomationState(AutomationAvailability.AVAILABLE, mode)

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
        if driver_input.brake > 0.0 or driver_input.steering_engaged:
            self._state = AutomationState(
                AutomationAvailability.AVAILABLE,
                DrivingControlMode.MANUAL,
            )
        return self._state

    def after_control_applied(self) -> None:
        return


class Source:
    last_snapshot_sample: ResearchSnapshotSample | None = None

    def get_host_timestamp(self) -> TimestampEnvelope:
        return TimestampEnvelope(HostClockTimestamp(100, 200))


class Persistence:
    def __init__(self) -> None:
        self.source = Source()
        self.events: list[tuple[str, str]] = []

    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None:
        self.events.append((event_type, payload_json))


def test_automation_priority_blocks_simultaneous_manual_input_before_request() -> None:
    vehicle = FakeVehicle()
    persistence = Persistence()
    subject = ResearchLaneChangeInteractionObserver(
        Interaction(DrivingControlMode.NOA_ACTIVE),
        CarlaAdjacentLaneAdapter(make_map(vehicle)),
        vehicle,
        persistence,
        LaneChangeCorrelation("GENERAL", None, None),
    )

    state = subject.update(
        DriverInput(
            brake=0.5,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )

    payload = json.loads(persistence.events[0][1])
    assert state.control_mode is DrivingControlMode.MANUAL
    assert payload["disposition"] == "REJECTED_MANUAL_PRIORITY"
    assert payload["dynamic_gap_status"] == "UNVERIFIED"
    assert payload["physical_completion_claimed"] is False


def test_p5_right_confirm_uses_one_request_with_coordinator_identity() -> None:
    vehicle = FakeVehicle()
    persistence = Persistence()
    subject = ResearchLaneChangeInteractionObserver(
        Interaction(DrivingControlMode.NOA_ACTIVE),
        CarlaAdjacentLaneAdapter(make_map(vehicle)),
        vehicle,
        persistence,
        LaneChangeCorrelation("GENERAL", "p5-event-1", 0.15),
    )

    subject.update(
        DriverInput(
            exit_confirm_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )

    assert len(persistence.events) == 1
    payload = json.loads(persistence.events[0][1])
    assert payload["context"] == "P5"
    assert payload["lc_event_id"] == "p5-event-1"
    assert payload["execution_status"] == "BLOCKED_SAFE_TM_EXECUTION_UNAVAILABLE"


def test_post_request_lane_identity_crossing_is_observed_without_completion_claim() -> (
    None
):
    vehicle = FakeVehicle()
    persistence = Persistence()
    subject = ResearchLaneChangeInteractionObserver(
        Interaction(DrivingControlMode.NOA_ACTIVE),
        CarlaAdjacentLaneAdapter(make_map(vehicle)),
        vehicle,
        persistence,
        LaneChangeCorrelation("GENERAL", None, None),
    )
    subject.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    persistence.source.last_snapshot_sample = ResearchSnapshotSample(
        TimestampEnvelope(HostClockTimestamp(110, 210)),
        carla.Transform(),
        36,
        0,
        -4,
        3.5,
        carla.Transform(),
        None,
        None,
        None,
        carla.VehicleControl(),
        "right",
        70.0,
    )

    subject.after_control_applied()

    event_type, payload_json = persistence.events[-1]
    payload = json.loads(payload_json)
    assert event_type == "lane_change_geometric_sample"
    assert payload["target_identity_transition"] is True
    assert payload["physical_boundary_crossing_verified"] is False
    assert payload["physical_completion_claimed"] is False


def test_tracker_records_every_sample_and_ends_on_manual_priority() -> None:
    vehicle = FakeVehicle()
    persistence = Persistence()
    subject = ResearchLaneChangeInteractionObserver(
        Interaction(DrivingControlMode.NOA_ACTIVE),
        CarlaAdjacentLaneAdapter(make_map(vehicle)),
        vehicle,
        persistence,
        LaneChangeCorrelation("GENERAL", None, None),
    )
    subject.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    sample = ResearchSnapshotSample(
        TimestampEnvelope(HostClockTimestamp(110, 210)),
        carla.Transform(carla.Location(x=2.0, y=1.0)),
        36,
        0,
        -3,
        3.5,
        carla.Transform(),
        -4,
        3.5,
        carla.Transform(),
        carla.VehicleControl(),
        "right",
        70.0,
    )
    persistence.source.last_snapshot_sample = sample
    subject.after_control_applied()
    subject.after_control_applied()
    subject.update(DriverInput(brake=0.5))

    event_types = [event_type for event_type, _ in persistence.events]
    assert event_types.count("lane_change_geometric_sample") == 2
    assert event_types[-1] == "lane_change_observation_end"
    payload = json.loads(persistence.events[-1][1])
    assert payload["reason"] == "MANUAL_PRIORITY"


def test_new_opposite_request_supersedes_existing_tracker() -> None:
    vehicle = FakeVehicle()
    persistence = Persistence()
    subject = ResearchLaneChangeInteractionObserver(
        Interaction(DrivingControlMode.NOA_ACTIVE),
        CarlaAdjacentLaneAdapter(make_map(vehicle)),
        vehicle,
        persistence,
        LaneChangeCorrelation("GENERAL", None, None),
    )
    subject.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    subject.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))

    end_payload = json.loads(persistence.events[1][1])
    assert persistence.events[1][0] == "lane_change_observation_end"
    assert end_payload["reason"] == "SUPERSEDED_BY_NEW_REQUEST"
