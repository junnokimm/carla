from __future__ import annotations

from dataclasses import dataclass

import carla
import pytest

from src.scenario.research_noa_lateral_validation import (
    LaneIdentity,
    LateralValidationCase,
    LateralValidationInitialCondition,
    LateralValidationPlacement,
    LateralValidationPreflightError,
    apply_initial_condition,
    validate_initial_state,
)


@dataclass(frozen=True, slots=True)
class FakeVelocity:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class FakeWaypoint:
    transform: carla.Transform
    road_id: int = 36
    section_id: int = 0
    lane_id: int = -2
    lane_width: float = 3.5
    lane_type: carla.LaneType = carla.LaneType.Driving


class FakeMap:
    def __init__(self, waypoint: FakeWaypoint, *, reject_corners: bool = False) -> None:
        self.waypoint = waypoint
        self.reject_corners = reject_corners
        self.calls = 0

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> FakeWaypoint | None:
        assert lane_type == carla.LaneType.Driving
        self.calls += 1
        if self.reject_corners and not project_to_road:
            return None
        return self.waypoint


class FakeVehicle:
    def __init__(
        self,
        transform: carla.Transform,
        *,
        speed_mps: float = 0.0,
        half_width_m: float = 0.9,
    ) -> None:
        self.transform = transform
        self.velocity = FakeVelocity(speed_mps, 0.0, 0.0)
        self.bounding_box = carla.BoundingBox(
            carla.Location(),
            carla.Vector3D(2.0, half_width_m, 0.7),
        )

    def get_transform(self) -> carla.Transform:
        return self.transform

    def get_velocity(self) -> FakeVelocity:
        return self.velocity


def test_position_cases_follow_lane_right_axis_sign() -> None:
    base = carla.Transform(carla.Location(10.0, 20.0, 0.3), carla.Rotation(yaw=90.0))
    left = LateralValidationInitialCondition(
        LateralValidationCase.POSITION_LEFT,
        position_offset_m=-0.25,
        heading_offset_rad=0.0,
    )
    right = LateralValidationInitialCondition(
        LateralValidationCase.POSITION_RIGHT,
        position_offset_m=0.25,
        heading_offset_rad=0.0,
    )

    left_transform = apply_initial_condition(base, left)
    right_transform = apply_initial_condition(base, right)

    assert left_transform.location.x == pytest.approx(10.25)
    assert right_transform.location.x == pytest.approx(9.75)
    assert left_transform.location.y == pytest.approx(20.0)
    assert right_transform.location.y == pytest.approx(20.0)


def test_heading_cases_follow_controller_heading_sign() -> None:
    base = carla.Transform(rotation=carla.Rotation(yaw=90.0))
    condition = LateralValidationInitialCondition(
        LateralValidationCase.HEADING_RIGHT,
        position_offset_m=0.0,
        heading_offset_rad=0.06,
    )

    transformed = apply_initial_condition(base, condition)

    assert transformed.rotation.yaw == pytest.approx(
        90.0 + 0.06 * 180.0 / 3.141592653589793
    )


def test_preflight_accepts_stationary_same_lane_footprint_and_reports_margin() -> None:
    transform = carla.Transform(carla.Location(0.0, 0.25, 0.3), carla.Rotation())
    waypoint = FakeWaypoint(carla.Transform(carla.Location(), carla.Rotation()))
    condition = LateralValidationInitialCondition(
        LateralValidationCase.POSITION_RIGHT,
        position_offset_m=0.25,
        heading_offset_rad=0.0,
    )

    result = validate_initial_state(
        FakeVehicle(transform),
        FakeMap(waypoint),
        LateralValidationPlacement(transform, condition, LaneIdentity(36, 0, -2)),
    )

    assert result.geometry.geometry.lateral_error_m == pytest.approx(0.25)
    assert result.geometry.geometry.heading_error_rad == pytest.approx(0.0)
    assert result.minimum_lane_margin_m == pytest.approx(0.6)
    assert result.speed_kmh == 0.0


def test_preflight_rejects_moving_vehicle() -> None:
    transform = carla.Transform()
    condition = LateralValidationInitialCondition(
        LateralValidationCase.BASELINE,
        position_offset_m=0.0,
        heading_offset_rad=0.0,
    )

    with pytest.raises(LateralValidationPreflightError, match="stationary"):
        validate_initial_state(
            FakeVehicle(transform, speed_mps=0.1),
            FakeMap(FakeWaypoint(transform)),
            LateralValidationPlacement(transform, condition, LaneIdentity(36, 0, -2)),
        )


def test_preflight_rejects_footprint_outside_same_lane() -> None:
    transform = carla.Transform()
    condition = LateralValidationInitialCondition(
        LateralValidationCase.BASELINE,
        position_offset_m=0.0,
        heading_offset_rad=0.0,
    )

    with pytest.raises(LateralValidationPreflightError, match="footprint"):
        validate_initial_state(
            FakeVehicle(transform),
            FakeMap(FakeWaypoint(transform), reject_corners=True),
            LateralValidationPlacement(transform, condition, LaneIdentity(36, 0, -2)),
        )


@pytest.mark.parametrize(
    ("case", "position", "heading"),
    [
        (LateralValidationCase.BASELINE, 0.25, 0.0),
        (LateralValidationCase.POSITION_LEFT, 0.25, 0.0),
        (LateralValidationCase.HEADING_LEFT, 0.0, 0.06),
        (LateralValidationCase.HEADING_RIGHT, 0.1, -0.06),
    ],
)
def test_initial_condition_rejects_case_offset_mismatch(
    case: LateralValidationCase,
    position: float,
    heading: float,
) -> None:
    with pytest.raises(ValueError):
        LateralValidationInitialCondition(case, position, heading)
