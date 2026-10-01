from __future__ import annotations

from dataclasses import dataclass

import carla
import pytest

from src.experiment.automation import AutomationAvailability, DrivingControlMode
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig, build_noa_runtime
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryAdapter, CarlaLocation
from src.vehicle.carla_noa_control import CarlaNoAControlBackend


class AutopilotTransitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FakeLocation:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class FakeRotation:
    yaw: float


@dataclass(frozen=True, slots=True)
class FakeTransform:
    location: FakeLocation
    rotation: FakeRotation


@dataclass(frozen=True, slots=True)
class FakeVelocity:
    x: float
    y: float
    z: float


class FakeWaypoint:
    def __init__(
        self,
        lane_id: int,
        transform: FakeTransform,
        *,
        left: FakeWaypoint | None = None,
        right: FakeWaypoint | None = None,
    ) -> None:
        self.lane_id = lane_id
        self.lane_type = carla.LaneType.Driving
        self.transform = transform
        self.left = left
        self.right = right

    def get_left_lane(self) -> FakeWaypoint | None:
        return self.left

    def get_right_lane(self) -> FakeWaypoint | None:
        return self.right


class FakeMap:
    def __init__(self, waypoint: FakeWaypoint) -> None:
        self.waypoint = waypoint

    def get_waypoint(
        self,
        location: CarlaLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> FakeWaypoint:
        assert project_to_road is True
        assert lane_type == carla.LaneType.Driving
        return self.waypoint


class FakeVehicle:
    def __init__(self, *, fail_autopilot: bool = False) -> None:
        self.transform = FakeTransform(
            FakeLocation(8.0, 4.0, 0.0),
            FakeRotation(0.0),
        )
        self.velocity = FakeVelocity(0.0, 0.0, 0.0)
        self.fail_autopilot = fail_autopilot
        self.autopilot_enabled = True
        self.autopilot_calls: list[bool] = []
        self.applied_controls: list[carla.VehicleControl] = []

    def set_autopilot(self, enabled: bool) -> None:
        self.autopilot_calls.append(enabled)
        if self.fail_autopilot:
            raise AutopilotTransitionError
        self.autopilot_enabled = enabled

    def get_transform(self) -> FakeTransform:
        return self.transform

    def get_velocity(self) -> FakeVelocity:
        return self.velocity

    def apply_control(self, control: carla.VehicleControl) -> None:
        self.applied_controls.append(control)


def make_control_config() -> NoAControlConfig:
    return NoAControlConfig(
        longitudinal=LongitudinalControlConfig(
            target_speed_kmh=36.0,
            speed_deadband_kmh=1.0,
            acceleration_gain=0.1,
            braking_gain=0.1,
            max_throttle=0.6,
            max_brake=0.7,
        ),
        lateral=LateralControlConfig(
            lateral_error_gain=0.2,
            heading_error_gain=0.5,
            lateral_deadband_m=0.1,
            heading_deadband_rad=0.05,
            max_steering=0.8,
        ),
    )


def make_map(vehicle: FakeVehicle) -> FakeMap:
    left = FakeWaypoint(-2, vehicle.transform)
    right = FakeWaypoint(-4, vehicle.transform)
    return FakeMap(FakeWaypoint(-3, vehicle.transform, left=left, right=right))


def test_build_composes_explicit_inactive_manual_runtime() -> None:
    vehicle = FakeVehicle()
    control_config = make_control_config()

    bundle = build_noa_runtime(vehicle, make_map(vehicle), control_config)

    assert (
        bundle.automation_runtime.state.availability is AutomationAvailability.AVAILABLE
    )
    assert bundle.automation_runtime.state.control_mode is DrivingControlMode.MANUAL
    assert bundle.control_backend.active is False
    assert vehicle.autopilot_calls == [False]
    assert vehicle.autopilot_enabled is False
    assert isinstance(bundle.control_backend, CarlaNoAControlBackend)
    assert isinstance(bundle.lane_geometry, CarlaLaneGeometryAdapter)
    assert isinstance(bundle.adjacent_lanes, CarlaAdjacentLaneAdapter)
    assert bundle.control_backend._longitudinal_config is control_config.longitudinal
    assert bundle.control_backend._lateral_config is control_config.lateral
    assert bundle.scheduler.runtime is bundle.automation_runtime
    assert bundle.scheduler.backend is bundle.ownership_backend


@pytest.mark.parametrize(
    "mode",
    [DrivingControlMode.MANUAL, DrivingControlMode.NOA_ACTIVE],
)
def test_repeated_mode_request_has_no_additional_ownership_side_effect(
    mode: DrivingControlMode,
) -> None:
    vehicle = FakeVehicle()
    bundle = build_noa_runtime(vehicle, make_map(vehicle), make_control_config())
    if mode is DrivingControlMode.NOA_ACTIVE:
        bundle.automation_runtime.request_control_mode(mode)
    vehicle.autopilot_calls.clear()

    bundle.automation_runtime.request_control_mode(mode)

    assert vehicle.autopilot_calls == []


def test_composed_scheduler_steps_only_while_noa_is_active() -> None:
    vehicle = FakeVehicle()
    bundle = build_noa_runtime(vehicle, make_map(vehicle), make_control_config())

    assert bundle.scheduler.update() is False
    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    assert bundle.scheduler.update() is True
    bundle.automation_runtime.request_control_mode(DrivingControlMode.MANUAL)
    assert bundle.scheduler.update() is False

    assert len(vehicle.applied_controls) == 1
    assert vehicle.autopilot_calls == [False, False, False]
    assert bundle.control_backend.active is False


def test_build_fails_before_exposing_runtime_when_autopilot_disable_fails() -> None:
    vehicle = FakeVehicle(fail_autopilot=True)

    with pytest.raises(AutopilotTransitionError):
        build_noa_runtime(vehicle, make_map(vehicle), make_control_config())

    assert vehicle.autopilot_calls == [False]
    assert vehicle.applied_controls == []
