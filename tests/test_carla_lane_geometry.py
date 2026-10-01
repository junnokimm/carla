from __future__ import annotations

from dataclasses import dataclass
from math import radians

import carla
import pytest

from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryAdapter,
    CarlaLaneGeometryUnavailableError,
    CarlaLocation,
)


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
class FakeWaypoint:
    transform: FakeTransform


@dataclass(frozen=True, slots=True)
class WaypointCall:
    location: CarlaLocation
    project_to_road: bool
    lane_type: carla.LaneType


class FakeVehicle:
    def __init__(self, transform: FakeTransform) -> None:
        self.transform = transform
        self.transform_call_count = 0

    def get_transform(self) -> FakeTransform:
        self.transform_call_count += 1
        return self.transform


class FakeMap:
    def __init__(self, waypoint: FakeWaypoint | None) -> None:
        self.waypoint = waypoint
        self.calls: list[WaypointCall] = []

    def get_waypoint(
        self,
        location: CarlaLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> FakeWaypoint | None:
        self.calls.append(WaypointCall(location, project_to_road, lane_type))
        return self.waypoint


def make_vehicle() -> FakeVehicle:
    return FakeVehicle(
        FakeTransform(
            location=FakeLocation(x=8.0, y=4.0, z=100.0),
            rotation=FakeRotation(yaw=20.0),
        )
    )


def make_waypoint() -> FakeWaypoint:
    return FakeWaypoint(
        FakeTransform(
            location=FakeLocation(x=7.0, y=3.0, z=-50.0),
            rotation=FakeRotation(yaw=10.0),
        )
    )


def test_observe_uses_vehicle_transform_and_current_driving_waypoint() -> None:
    vehicle = make_vehicle()
    carla_map = FakeMap(make_waypoint())
    adapter = CarlaLaneGeometryAdapter(carla_map)

    adapter.observe(vehicle)

    assert vehicle.transform_call_count == 1
    assert carla_map.calls == [
        WaypointCall(
            location=vehicle.transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
    ]


def test_observe_uses_waypoint_transform_and_matches_pure_geometry() -> None:
    vehicle = make_vehicle()
    waypoint = make_waypoint()
    adapter = CarlaLaneGeometryAdapter(FakeMap(waypoint))
    expected = compute_lane_relative_geometry(
        PlanarPose(x=8.0, y=4.0, yaw_rad=radians(20.0)),
        PlanarPose(x=7.0, y=3.0, yaw_rad=radians(10.0)),
    )

    observation = adapter.observe(vehicle)

    assert observation == expected


def test_adapter_ignores_altitude_in_lane_geometry() -> None:
    observation = CarlaLaneGeometryAdapter(FakeMap(make_waypoint())).observe(
        make_vehicle()
    )
    expected = compute_lane_relative_geometry(
        PlanarPose(x=8.0, y=4.0, yaw_rad=radians(20.0)),
        PlanarPose(x=7.0, y=3.0, yaw_rad=radians(10.0)),
    )

    assert observation == expected


def test_unavailable_map_fails_clearly() -> None:
    with pytest.raises(CarlaLaneGeometryUnavailableError) as caught:
        CarlaLaneGeometryAdapter(None)

    assert caught.value.resource == "map"


def test_unavailable_vehicle_fails_clearly() -> None:
    adapter = CarlaLaneGeometryAdapter(FakeMap(make_waypoint()))

    with pytest.raises(CarlaLaneGeometryUnavailableError) as caught:
        adapter.observe(None)

    assert caught.value.resource == "vehicle"


def test_unavailable_waypoint_fails_clearly() -> None:
    adapter = CarlaLaneGeometryAdapter(FakeMap(None))

    with pytest.raises(CarlaLaneGeometryUnavailableError) as caught:
        adapter.observe(make_vehicle())

    assert caught.value.resource == "driving lane waypoint"
