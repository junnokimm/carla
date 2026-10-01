from __future__ import annotations

from dataclasses import dataclass

import carla
import pytest

from src.experiment.lane_change import AdjacentLaneObservation
from src.vehicle.carla_adjacent_lanes import (
    CarlaAdjacentLaneAdapter,
    CarlaAdjacentLaneLocation,
    CarlaAdjacentLaneUnavailableError,
)


@dataclass(frozen=True, slots=True)
class FakeLocation:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class FakeTransform:
    location: FakeLocation


class FakeWaypoint:
    def __init__(
        self,
        lane_id: int,
        lane_type: carla.LaneType,
        *,
        left: FakeWaypoint | None = None,
        right: FakeWaypoint | None = None,
    ) -> None:
        self.lane_id = lane_id
        self.lane_type = lane_type
        self.left = left
        self.right = right
        self.left_call_count = 0
        self.right_call_count = 0

    def get_left_lane(self) -> FakeWaypoint | None:
        self.left_call_count += 1
        return self.left

    def get_right_lane(self) -> FakeWaypoint | None:
        self.right_call_count += 1
        return self.right


class FakeVehicle:
    def __init__(self, transform: FakeTransform) -> None:
        self.transform = transform
        self.transform_call_count = 0

    def get_transform(self) -> FakeTransform:
        self.transform_call_count += 1
        return self.transform


@dataclass(frozen=True, slots=True)
class WaypointCall:
    location: CarlaAdjacentLaneLocation
    project_to_road: bool
    lane_type: carla.LaneType


class FakeMap:
    def __init__(self, waypoint: FakeWaypoint | None) -> None:
        self.waypoint = waypoint
        self.calls: list[WaypointCall] = []

    def get_waypoint(
        self,
        location: CarlaAdjacentLaneLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> FakeWaypoint | None:
        self.calls.append(WaypointCall(location, project_to_road, lane_type))
        return self.waypoint


def make_vehicle() -> FakeVehicle:
    return FakeVehicle(FakeTransform(FakeLocation(8.0, 4.0, 100.0)))


def test_observe_preserves_both_immediate_driving_lane_ids() -> None:
    left = FakeWaypoint(-2, carla.LaneType.Driving)
    right = FakeWaypoint(-4, carla.LaneType.Driving)
    current = FakeWaypoint(-3, carla.LaneType.Driving, left=left, right=right)
    carla_map = FakeMap(current)
    vehicle = make_vehicle()

    result = CarlaAdjacentLaneAdapter(carla_map).observe(vehicle)

    assert result == AdjacentLaneObservation(-3, -2, -4)
    assert vehicle.transform_call_count == 1
    assert carla_map.calls == [
        WaypointCall(vehicle.transform.location, True, carla.LaneType.Driving)
    ]
    assert current.left_call_count == 1
    assert current.right_call_count == 1


def test_observe_preserves_right_lane_when_left_is_unavailable() -> None:
    right = FakeWaypoint(-4, carla.LaneType.Driving)
    current = FakeWaypoint(-3, carla.LaneType.Driving, right=right)

    result = CarlaAdjacentLaneAdapter(FakeMap(current)).observe(make_vehicle())

    assert result == AdjacentLaneObservation(-3, None, -4)


def test_observe_rejects_non_driving_lane_without_searching_beyond_it() -> None:
    further_driving = FakeWaypoint(2, carla.LaneType.Driving)
    median = FakeWaypoint(1, carla.LaneType.Median, left=further_driving)
    current = FakeWaypoint(-2, carla.LaneType.Driving, left=median)

    result = CarlaAdjacentLaneAdapter(FakeMap(current)).observe(make_vehicle())

    assert result == AdjacentLaneObservation(-2, None, None)
    assert median.left_call_count == 0
    assert median.right_call_count == 0


def test_observe_accepts_lane_type_containing_driving_flag() -> None:
    composite_type = carla.LaneType.Driving | carla.LaneType.Shoulder
    left = FakeWaypoint(2, composite_type)
    current = FakeWaypoint(-3, carla.LaneType.Driving, left=left)

    result = CarlaAdjacentLaneAdapter(FakeMap(current)).observe(make_vehicle())

    assert result.left_lane_id == 2


def test_unavailable_map_is_rejected_at_adapter_boundary() -> None:
    with pytest.raises(CarlaAdjacentLaneUnavailableError) as caught:
        CarlaAdjacentLaneAdapter(None)

    assert caught.value.resource == "map"


def test_unavailable_vehicle_is_rejected_at_adapter_boundary() -> None:
    current = FakeWaypoint(-3, carla.LaneType.Driving)
    adapter = CarlaAdjacentLaneAdapter(FakeMap(current))

    with pytest.raises(CarlaAdjacentLaneUnavailableError) as caught:
        adapter.observe(None)

    assert caught.value.resource == "vehicle"


def test_missing_current_driving_waypoint_is_rejected() -> None:
    adapter = CarlaAdjacentLaneAdapter(FakeMap(None))

    with pytest.raises(CarlaAdjacentLaneUnavailableError) as caught:
        adapter.observe(make_vehicle())

    assert caught.value.resource == "driving lane waypoint"
