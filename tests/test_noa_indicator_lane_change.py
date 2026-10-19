from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import carla
import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.lane_change import LaneChangeDirection
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
)
from tests.test_driver_view import FakeWorld
from tests.test_lane_change_execution_lifecycle import (
    drive_to_completion,
    make_rig,
    sample,
)
from tests.test_p8_tsxw_ordering import sample as wheel_sample


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
@pytest.mark.parametrize("device", ["keyboard", "wheel"])
@pytest.mark.parametrize("brake", [0.0, 0.02, 0.049])
@pytest.mark.parametrize("stage", ["TRAINING", "M1", "M2"])
def test_real_input_path_submits_once_and_completion_keeps_noa(
    monkeypatch,
    direction,
    device,
    brake,
    stage,
) -> None:
    rig = make_rig(direction, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.LowBeam
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    view = ResearchLiveDriverView(
        FakeWorld(),
        rig.vehicle,
        ResearchDriverViewConfig(
            automation_state_source=lambda: rig.observer.state,
        ),
    )
    right = direction is LaneChangeDirection.RIGHT

    class Wheel:
        def poll(self, _events):
            return wheel_sample(
                brake=brake,
                left_pressed=device == "wheel" and not right,
                left_held=device == "wheel" and not right,
                right_pressed=device == "wheel" and right,
                right_held=device == "wheel" and right,
            )

    view._tsxw_input = Wheel()
    events = (
        []
        if device == "wheel"
        else [
            pygame.event.Event(
                pygame.KEYDOWN,
                key=pygame.K_x if right else pygame.K_z,
                repeat=0,
            )
        ]
    )
    driver_input = view._collect_driver_input(events)
    rig.observer.update(replace(driver_input, stage=stage))
    view._handle_mode_events(view._events_for_mode_handling(events))
    assert rig.traffic_manager.calls.count(("force_lane_change", right)) == 1
    drive_to_completion(rig, direction)
    assert rig.observer.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert rig.backend.active
    assert not rig.observer.lane_change_in_progress
    assert not rig.vehicle.current_light_state & (
        carla.VehicleLightState.LeftBlinker | carla.VehicleLightState.RightBlinker
    )
    assert rig.vehicle.current_light_state & carla.VehicleLightState.LowBeam


@pytest.mark.parametrize(
    "override",
    [
        DriverInput(brake=0.05),
        DriverInput(brake=1.0),
        DriverInput(steering_engaged=True),
    ],
)
def test_driver_override_still_wins_over_signal(override) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    rig.observer.update(
        replace(override, lane_change_direction=LaneChangeDirection.RIGHT)
    )
    assert rig.observer.state.control_mode is DrivingControlMode.MANUAL
    assert not any(name == "force_lane_change" for name, _ in rig.traffic_manager.calls)


def test_delayed_signal_readback_waits_then_submits_once() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.LowBeam
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    rig.observer.after_control_applied()
    assert not rig.backend.lane_change_in_flight
    rig.vehicle.current_light_state |= carla.VehicleLightState.RightBlinker
    rig.observer.after_control_applied()
    rig.observer.after_control_applied()
    assert rig.traffic_manager.calls.count(("force_lane_change", True)) == 1
    drive_to_completion(rig, LaneChangeDirection.RIGHT)
    assert rig.observer.state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_signal_never_observed_expires_without_stopping_noa() -> None:
    rig = make_rig(LaneChangeDirection.LEFT, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.LowBeam
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))
    rig.persistence.source.host_ns += 1_000_000_001
    rig.observer.after_control_applied()
    assert not rig.observer.lane_change_in_progress
    assert rig.observer.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert not rig.backend.lane_change_in_flight


def test_rejected_old_topology_can_resume_only_on_original_verified_pair() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=0.0,
        road_id=999,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0)),
    )
    rig.observer.after_control_applied()
    assert rig.observer.lane_change_in_progress
    assert rig.vehicle.current_light_state & carla.VehicleLightState.RightBlinker
    # Same bad geometry cannot manufacture completion or unlock a second force.
    rig.observer.after_control_applied()
    assert rig.observer.lane_change_in_progress
    assert rig.observer._tracker.unverified_reason == "GEOMETRY_CONTINUITY_UNAVAILABLE"
    drive_to_completion(rig, LaneChangeDirection.RIGHT)
    assert not rig.observer.lane_change_in_progress
    assert any(payload.get("lifecycle") == "OBSERVATION_RESUMED"
               for _, payload in rig.persistence.events)


def test_other_module_actors_do_not_globally_block_requests(monkeypatch) -> None:
    rig = make_rig(LaneChangeDirection.LEFT, submit_general=False)
    # Inventory no longer participates in force admission; actors in other parts
    # of the map (including M2 props) must not veto a valid adjacent-lane request.
    monkeypatch.setattr(
        rig.backend._lane_changes._world,
        "get_actors",
        lambda: (_ for _ in ()).throw(AssertionError("global inventory queried")),
    )
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))
    drive_to_completion(rig, LaneChangeDirection.LEFT)
    assert rig.observer.state.control_mode is DrivingControlMode.NOA_ACTIVE
