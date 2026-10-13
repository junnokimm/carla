from collections import defaultdict
from dataclasses import replace

import carla
import pygame
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.lane_change import LaneChangeDirection
from src.scenario.research_noa_view import ResearchDriverView, ResearchDriverViewConfig
from tests.test_driver_view import FakeWorld
from tests.test_lane_change_execution_lifecycle import make_rig, sample


def test_following_curved_source_center_is_not_lateral_lane_change() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11, y=5.0, lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=5.0)),
        left_lane_id=-2,
        left_transform=carla.Transform(carla.Location(x=10.0, y=1.5)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=8.5)),
    )
    rig.observer.after_control_applied()
    assert not any(
        payload.get("lifecycle") == "IN_PROGRESS"
        for _, payload in rig.persistence.events
    )


def test_snapshot_acquired_before_submission_is_not_movement_evidence() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    observed = sample(
        11, y=1.8, lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )
    assert observed.timestamp.carla_snapshot is not None
    rig.persistence.source.last_snapshot_sample = replace(
        observed,
        timestamp=replace(
            observed.timestamp,
            carla_snapshot=replace(
                observed.timestamp.carla_snapshot,
                host_capture_started_monotonic_ns=1,
                host_capture_completed_monotonic_ns=2,
            ),
        ),
    )
    rig.observer.after_control_applied()
    assert not any(
        payload.get("lifecycle") == "IN_PROGRESS"
        for _, payload in rig.persistence.events
    )


def test_indicator_setter_failure_prevents_request_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.NONE
    viewer = ResearchDriverView(FakeWorld(), rig.vehicle, ResearchDriverViewConfig(
        automation_state_source=lambda: rig.observer.state,
    ))
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))

    def fail_lights(state: carla.VehicleLightState) -> None:
        raise RuntimeError("signal setter failed")

    monkeypatch.setattr(rig.vehicle, "set_light_state", fail_lights)
    with pytest.raises(RuntimeError, match="signal setter failed"):
        driver_input = viewer._collect_driver_input(
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x)]
        )
        rig.observer.update(driver_input)
    assert not any(name == "force_lane_change" for name, _ in rig.traffic_manager.calls)


def test_indicator_is_applied_before_force_and_not_toggled_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.LowBeam
    viewer = ResearchDriverView(FakeWorld(), rig.vehicle, ResearchDriverViewConfig(
        automation_state_source=lambda: rig.observer.state,
    ))
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x)]
    driver_input = viewer._collect_driver_input(events)
    assert rig.vehicle.get_light_state() & carla.VehicleLightState.RightBlinker
    rig.observer.update(driver_input)
    viewer._handle_mode_events(events)
    assert rig.vehicle.get_light_state() == (
        carla.VehicleLightState.LowBeam | carla.VehicleLightState.RightBlinker
    )
    assert [value for name, value in rig.traffic_manager.calls if name == "force_lane_change"] == [True]


def test_unobserved_indicator_on_blocks_force() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.NONE
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    assert not any(name == "force_lane_change" for name, _ in rig.traffic_manager.calls)


def test_source_lateral_error_preserves_nonzero_baseline_offset() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    baseline = rig.persistence.source.last_snapshot_sample
    assert baseline is not None
    rig.persistence.source.last_snapshot_sample = replace(
        baseline, transform=carla.Transform(carla.Location(x=10.0, y=0.15))
    )
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    rig.persistence.source.last_snapshot_sample = sample(
        11, y=1.0, lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )
    rig.observer.after_control_applied()
    observed = next(payload for kind, payload in rig.persistence.events if kind == "lane_change_geometric_sample")
    assert observed["source_lateral_error_m"] == pytest.approx(1.0)
