from __future__ import annotations

from pathlib import Path

import carla
import pytest

from src.experiment.exit_route import ExitRoute, ExitRouteError, RoutePoint
from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.vehicle.carla_exit_route import (
    SwitchableRouteLaneGeometrySource,
    load_exit_route_manifest,
    validate_exit_route_map,
)
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext


class OrdinaryGeometry:
    def __init__(self) -> None:
        pose = PlanarPose(0.0, 0.0, 0.0)
        self.context = CarlaLaneGeometryContext(
            LaneGeometryObservation(0.25, 0.1),
            1,
            0,
            -3,
            3.5,
            pose,
            pose,
        )

    def observe(self, vehicle: Vehicle) -> LaneGeometryObservation:
        return self.context.geometry

    def observe_with_context(self, vehicle: Vehicle) -> CarlaLaneGeometryContext:
        return self.context


class Vehicle:
    def __init__(self, transform: carla.Transform) -> None:
        self.transform = transform

    def get_transform(self) -> carla.Transform:
        return self.transform


def route() -> ExitRoute:
    return ExitRoute(
        "route",
        "version",
        "Town04",
        -3,
        -4,
        10.0,
        (
            RoutePoint(0.0, 0.0, 0.0, 0.0, 39, 0, -3, 3.5),
            RoutePoint(9.0, 0.0, 0.0, 9.0, 39, 0, -4, 3.5),
            RoutePoint(10.0, 0.0, 0.0, 10.0, 1191, 1, -2, 3.5),
        ),
        ((1184, 0, -4),),
        ((1191, 1, -2),),
    )


def test_reference_delegates_when_inactive_and_freezes_route_when_armed() -> None:
    ordinary = OrdinaryGeometry()
    source = SwitchableRouteLaneGeometrySource(ordinary)
    vehicle = Vehicle(carla.Transform(carla.Location(x=5.0, y=1.0)))

    inactive = source.observe_with_context(vehicle)
    source.arm(route())
    active = source.observe_with_context(vehicle)
    source.cancel()
    restored = source.observe_with_context(vehicle)

    assert inactive is ordinary.context
    assert active.lane_id == -3
    assert active.geometry.lateral_error_m == 1.0
    assert restored is ordinary.context


def test_checked_in_town04_manifest_has_versioned_exit_and_through_topology() -> None:
    manifest = load_exit_route_manifest(Path("config/town04_exit_routes_dev_v3.json"))

    route_39 = manifest.route("town04-exit-39")
    route_47 = manifest.route("town04-exit-47")

    assert manifest.map_name == "Carla/Maps/Town04"
    assert route_39.fork_distance_m > 1000.0
    assert route_47.fork_distance_m > 1000.0
    assert (1191, 1, -2) in route_39.exit_segments
    assert (1184, 0, -4) in route_39.through_segments
    assert (782, 1, -2) in route_47.exit_segments
    assert (774, 0, -4) in route_47.through_segments
    assert route_39.exit_segments[0] not in route_39.through_segments
    assert route_47.exit_segments[0] not in route_47.through_segments


def test_armed_reference_tracks_forward_blended_route_and_rejects_far_spawn() -> None:
    selected = ExitRoute(
        "moving",
        "v1",
        "Town04",
        -3,
        -4,
        30.0,
        (
            RoutePoint(0.0, 0.0, 0.0, 0.0, 39, 0, -3, 3.5),
            RoutePoint(10.0, 0.0, 0.0, 10.0, 39, 0, -3, 3.5),
            RoutePoint(20.0, -1.75, 0.0, 20.0, 39, 0, -4, 3.5),
            RoutePoint(30.0, -3.5, 0.0, 30.0, 39, 0, -4, 3.5),
            RoutePoint(40.0, -3.5, 0.0, 40.0, 1191, 1, -2, 3.5),
        ),
        (),
        ((1191, 1, -2),),
        initiation_end_m=10.0,
        completion_distance_m=40.0,
    )
    source = SwitchableRouteLaneGeometrySource(OrdinaryGeometry())
    vehicle = Vehicle(carla.Transform(carla.Location(x=5.0, y=1.0)))
    source.arm(selected, 0)

    source_context = source.observe_with_context(vehicle)
    vehicle.transform = carla.Transform(carla.Location(x=25.0, y=-2.5))
    blended_context = source.observe_with_context(vehicle)

    assert source_context.geometry.lateral_error_m == pytest.approx(1.0)
    assert blended_context.waypoint_pose.y < source_context.waypoint_pose.y
    vehicle.transform = carla.Transform(carla.Location(x=25.0, y=100.0))
    with pytest.raises(ExitRouteError):
        source.observe_with_context(vehicle)


class ValidationWaypoint:
    def __init__(
        self, lane_id: int, *, right: ValidationWaypoint | None = None
    ) -> None:
        self.road_id = 39
        self.section_id = 0
        self.lane_id = lane_id
        self.lane_width = 3.5
        self.lane_type = carla.LaneType.Driving
        self.lane_change = carla.LaneChange.Right
        self.transform = carla.Transform(
            carla.Location(x=10.0 if lane_id == -4 else 0.0)
        )
        self.right = right

    def get_right_lane(self) -> ValidationWaypoint | None:
        return self.right


class ValidationMap:
    def __init__(
        self,
        waypoint: ValidationWaypoint,
        target: ValidationWaypoint | None = None,
    ) -> None:
        self.waypoint = waypoint
        self.target = target

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> ValidationWaypoint:
        if self.target is not None and location.x >= 9.0:
            return self.target
        return self.waypoint

    def generate_waypoints(self, distance: float) -> list[ValidationWaypoint]:
        return [
            self.waypoint,
            *([] if self.target is None else [self.target]),
        ]


class ValidationVehicle:
    bounding_box = carla.BoundingBox(carla.Location(), carla.Vector3D(2.0, 0.8, 0.7))


def test_runtime_route_validation_checks_directional_right_adjacency() -> None:
    selected = ExitRoute(
        "validated",
        "v1",
        "Town04",
        -3,
        -4,
        10.0,
        (
            RoutePoint(0.0, 0.0, 0.0, 0.0, 39, 0, -3, 3.5),
            RoutePoint(10.0, -3.5, 0.0, 10.0, 39, 0, -4, 3.5),
        ),
        (),
        (),
        initiation_end_m=5.0,
        completion_distance_m=10.0,
    )
    target = ValidationWaypoint(-4)

    validate_exit_route_map(
        selected,
        ValidationMap(ValidationWaypoint(-3, right=target), target),
        ValidationVehicle(),
    )

    with pytest.raises(ExitRouteError):
        validate_exit_route_map(
            selected,
            ValidationMap(ValidationWaypoint(-3), target),
            ValidationVehicle(),
        )
