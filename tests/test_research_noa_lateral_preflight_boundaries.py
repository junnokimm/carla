from __future__ import annotations

from dataclasses import dataclass
from math import inf, nan

import carla
import pytest

from src.scenario.research_noa_lateral_validation import (
    LaneIdentity,
    LateralValidationCase,
    LateralValidationInitialCondition,
    LateralValidationPlacement,
    LateralValidationPreflightError,
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
    road_id: int
    section_id: int
    lane_id: int
    lane_width: float
    lane_type: carla.LaneType = carla.LaneType.Driving


class LocationAwareMap:
    def __init__(
        self,
        center: FakeWaypoint,
        corners: tuple[FakeWaypoint, ...],
    ) -> None:
        self.center = center
        self.corners = iter(corners)

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> FakeWaypoint:
        assert lane_type == carla.LaneType.Driving
        return self.center if project_to_road else next(self.corners)


class FixedVerticesBoundingBox:
    def __init__(self, vertices: tuple[carla.Location, ...]) -> None:
        self.vertices = vertices

    def get_world_vertices(self, transform: carla.Transform) -> list[carla.Location]:
        return list(self.vertices)


class FakeVehicle:
    def __init__(
        self,
        transform: carla.Transform,
        velocity: FakeVelocity,
        vertices: tuple[carla.Location, ...],
    ) -> None:
        self.transform = transform
        self.velocity = velocity
        self.bounding_box = FixedVerticesBoundingBox(vertices)

    def get_transform(self) -> carla.Transform:
        return self.transform

    def get_velocity(self) -> FakeVelocity:
        return self.velocity


def _condition() -> LateralValidationInitialCondition:
    return LateralValidationInitialCondition(LateralValidationCase.BASELINE, 0.0, 0.0)


def _waypoint(
    *,
    y: float = 0.0,
    lane_id: int = -2,
    width: float = 3.5,
) -> FakeWaypoint:
    return FakeWaypoint(
        carla.Transform(carla.Location(y=y), carla.Rotation()),
        road_id=36,
        section_id=0,
        lane_id=lane_id,
        lane_width=width,
    )


def _placement(expected: carla.Transform) -> LateralValidationPlacement:
    return LateralValidationPlacement(
        expected,
        _condition(),
        LaneIdentity(36, 0, -2),
    )


@pytest.mark.parametrize(
    ("case", "position", "heading"),
    [
        (LateralValidationCase.POSITION_LEFT, -inf, 0.0),
        (LateralValidationCase.POSITION_RIGHT, inf, 0.0),
        (LateralValidationCase.HEADING_LEFT, 0.0, -inf),
        (LateralValidationCase.HEADING_RIGHT, 0.0, inf),
        (LateralValidationCase.BASELINE, nan, 0.0),
        (LateralValidationCase.BASELINE, 0.0, nan),
    ],
)
def test_initial_condition_rejects_nonfinite_offsets_before_spawn(
    case: LateralValidationCase,
    position: float,
    heading: float,
) -> None:
    with pytest.raises(ValueError):
        LateralValidationInitialCondition(case, position, heading)


def test_preflight_rejects_nonfinite_placement_delta() -> None:
    actual = carla.Transform(carla.Location(x=nan), carla.Rotation())
    expected = carla.Transform()
    vehicle = FakeVehicle(actual, FakeVelocity(0.0, 0.0, 0.0), ())

    with pytest.raises(LateralValidationPreflightError, match="placement"):
        validate_initial_state(
            vehicle,
            LocationAwareMap(_waypoint(), ()),
            _placement(expected),
        )


def test_preflight_rejects_nonfinite_speed() -> None:
    transform = carla.Transform()
    vehicle = FakeVehicle(transform, FakeVelocity(nan, 0.0, 0.0), ())

    with pytest.raises(LateralValidationPreflightError, match="speed"):
        validate_initial_state(
            vehicle,
            LocationAwareMap(_waypoint(), ()),
            _placement(transform),
        )


def test_preflight_accepts_equivalent_yaw_across_wrap_boundary() -> None:
    actual = carla.Transform(rotation=carla.Rotation(yaw=180.0))
    expected = carla.Transform(rotation=carla.Rotation(yaw=-180.0))
    vertex = carla.Location()
    waypoint = _waypoint()
    vehicle = FakeVehicle(actual, FakeVelocity(0.0, 0.0, 0.0), (vertex,))

    result = validate_initial_state(
        vehicle,
        LocationAwareMap(waypoint, (waypoint,)),
        _placement(expected),
    )

    assert result.speed_kmh == 0.0


def test_preflight_rejects_wholly_contained_adjacent_lane_footprint() -> None:
    transform = carla.Transform()
    vertices = (carla.Location(y=0.1), carla.Location(y=-0.1))
    adjacent = _waypoint(lane_id=-1)
    vehicle = FakeVehicle(transform, FakeVelocity(0.0, 0.0, 0.0), vertices)

    with pytest.raises(LateralValidationPreflightError, match="initial lane"):
        validate_initial_state(
            vehicle,
            LocationAwareMap(adjacent, (adjacent, adjacent)),
            _placement(transform),
        )


def test_preflight_uses_each_corner_local_transform_and_width_for_margin() -> None:
    transform = carla.Transform()
    vertices = (carla.Location(y=1.4), carla.Location(y=-1.4))
    left_local = _waypoint(y=1.3, width=2.0)
    right_local = _waypoint(y=-1.2, width=1.0)
    vehicle = FakeVehicle(transform, FakeVelocity(0.0, 0.0, 0.0), vertices)

    result = validate_initial_state(
        vehicle,
        LocationAwareMap(_waypoint(), (left_local, right_local)),
        _placement(transform),
    )

    assert result.minimum_lane_margin_m == pytest.approx(0.3)


@pytest.mark.parametrize("width", [nan, inf, -inf])
def test_preflight_rejects_nonfinite_corner_lane_width(width: float) -> None:
    transform = carla.Transform()
    vertex = carla.Location(y=0.1)
    vehicle = FakeVehicle(
        transform,
        FakeVelocity(0.0, 0.0, 0.0),
        (vertex,),
    )

    with pytest.raises(LateralValidationPreflightError, match="finite"):
        validate_initial_state(
            vehicle,
            LocationAwareMap(_waypoint(), (_waypoint(width=width),)),
            _placement(transform),
        )


def test_preflight_rejects_nonfinite_local_geometry() -> None:
    transform = carla.Transform()
    vertex = carla.Location(y=0.1)
    vehicle = FakeVehicle(transform, FakeVelocity(0.0, 0.0, 0.0), (vertex,))

    with pytest.raises(LateralValidationPreflightError, match="finite"):
        validate_initial_state(
            vehicle,
            LocationAwareMap(_waypoint(), (_waypoint(y=nan),)),
            _placement(transform),
        )
