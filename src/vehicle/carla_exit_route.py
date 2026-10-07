from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import atan2, cos, hypot, pi, radians, sin
from pathlib import Path
from typing import Protocol

import carla

from src.experiment.exit_route import (
    ExitRoute,
    ExitRouteError,
    RoutePoint,
    RouteProjection,
    project_route,
)
from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryContext,
    CarlaVehicle,
)


class DetailedLaneGeometrySource(Protocol):
    def observe(self, vehicle: CarlaVehicle | None) -> LaneGeometryObservation: ...

    def observe_with_context(
        self, vehicle: CarlaVehicle | None
    ) -> CarlaLaneGeometryContext: ...


class ExitRouteMap(Protocol):
    def generate_waypoints(self, distance: float) -> list[carla.Waypoint]: ...

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> carla.Waypoint | None: ...


class ExitRouteVehicle(Protocol):
    @property
    def bounding_box(self) -> carla.BoundingBox: ...


@dataclass(frozen=True, slots=True)
class ExitRouteManifest:
    manifest_version: str
    map_name: str
    routes: tuple[ExitRoute, ...]

    def route(self, route_id: str) -> ExitRoute:
        for candidate in self.routes:
            if candidate.route_id == route_id:
                return candidate
        raise ExitRouteError(f"route {route_id!r} is absent from manifest")


class SwitchableRouteLaneGeometrySource:
    def __init__(self, ordinary: DetailedLaneGeometrySource) -> None:
        self._ordinary = ordinary
        self._active_route: ExitRoute | None = None
        self._cursor: RouteProjection | None = None
        self._start_index = 0

    @property
    def active_route(self) -> ExitRoute | None:
        return self._active_route

    def arm(self, route: ExitRoute, point_index: int = 0) -> None:
        self._active_route = route
        self._cursor = None
        self._start_index = point_index

    def cancel(self) -> None:
        self._active_route = None
        self._cursor = None
        self._start_index = 0

    def observe(self, vehicle: CarlaVehicle | None) -> LaneGeometryObservation:
        return self.observe_with_context(vehicle).geometry

    def observe_with_context(
        self, vehicle: CarlaVehicle | None
    ) -> CarlaLaneGeometryContext:
        route = self._active_route
        if route is None:
            return self._ordinary.observe_with_context(vehicle)
        if vehicle is None:
            raise ExitRouteError("route geometry requires a vehicle")
        transform = vehicle.get_transform()
        vehicle_pose = PlanarPose(
            float(transform.location.x),
            float(transform.location.y),
            radians(float(transform.rotation.yaw)),
        )
        projection = project_route(
            route,
            vehicle_pose,
            cursor=self._cursor,
            start_index=self._start_index,
            corridor_m=max(point.lane_width_m * 1.5 for point in route.points),
        )
        self._cursor = projection
        point = route.points[projection.point_index]
        return CarlaLaneGeometryContext(
            geometry=LaneGeometryObservation(
                projection.lateral_offset_m,
                projection.heading_error_rad,
            ),
            road_id=point.road_id,
            section_id=point.section_id,
            lane_id=point.lane_id,
            lane_width_m=point.lane_width_m,
            vehicle_pose=vehicle_pose,
            waypoint_pose=projection.reference_pose,
        )


def load_exit_route_manifest(path: Path) -> ExitRouteManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    routes = tuple(_parse_route(item) for item in raw["routes"])
    manifest = ExitRouteManifest(raw["manifest_version"], raw["map_name"], routes)
    if len({route.route_id for route in routes}) != len(routes):
        raise ExitRouteError("route manifest contains duplicate route identities")
    for route, item in zip(routes, raw["routes"], strict=True):
        _validate_route_geometry(route, float(raw["sample_step_m"]))
        if route.map_name != manifest.map_name:
            raise ExitRouteError("route map does not match manifest map")
        ordered_links = []
        for point in route.points:
            identity = [point.road_id, point.section_id, point.lane_id]
            if not ordered_links or ordered_links[-1] != identity:
                ordered_links.append(identity)
        if item.get("ordered_links") != ordered_links:
            raise ExitRouteError("ordered route links do not match route points")
        topology = item.get("topology_links")
        if not isinstance(topology, dict):
            raise ExitRouteError("route topology links are missing")
        through = {tuple(value) for value in topology.get("through", ())}
        exits = {tuple(value) for value in topology.get("exit", ())}
        if not set(route.through_segments).issubset(through):
            raise ExitRouteError("through segments do not match route topology")
        if not set(route.exit_segments).issubset(exits):
            raise ExitRouteError("exit segments do not match route topology")
    expected = raw["route_data_sha256"]
    actual = route_data_sha256(routes)
    if actual != expected:
        raise ExitRouteError("route manifest hash does not match route data")
    return manifest


def _validate_route_geometry(route: ExitRoute, sample_step_m: float) -> None:
    for previous, current in zip(route.points, route.points[1:], strict=False):
        spacing = hypot(current.x - previous.x, current.y - previous.y)
        if spacing > sample_step_m * 1.25:
            raise ExitRouteError("route sample spacing exceeds development criterion")
        if abs((current.distance_m - previous.distance_m) - spacing) > 0.02:
            raise ExitRouteError("route station does not match sample spacing")
        segment_yaw = atan2(current.y - previous.y, current.x - previous.x)
        tangent_delta = abs(
            atan2(
                sin(segment_yaw - previous.yaw_rad),
                cos(segment_yaw - previous.yaw_rad),
            )
        )
        if tangent_delta > 0.2:
            raise ExitRouteError(
                "route tangent continuity exceeds development criterion"
            )


def validate_exit_route_map(
    route: ExitRoute,
    carla_map: ExitRouteMap,
    vehicle: ExitRouteVehicle,
) -> None:
    initiation_end = route.initiation_end_m or route.fork_distance_m
    envelope = tuple(
        point
        for point in route.points
        if route.initiation_start_m <= point.distance_m <= initiation_end
    )
    if not any(point.lane_id == route.source_lane_id for point in envelope):
        raise ExitRouteError("route maneuver envelope has no source-lane point")
    target_point = next(
        point
        for point in reversed(route.points)
        if point.distance_m <= route.fork_distance_m
        and point.lane_id == route.target_lane_id
    )
    anchors = [*envelope, target_point]
    for segment in route.exit_segments:
        anchor = next(
            (point for point in route.points if point.identity == segment),
            None,
        )
        if anchor is not None:
            anchors.append(anchor)
    map_waypoints = carla_map.generate_waypoints(2.0)
    waypoints: list[carla.Waypoint] = []
    for point in anchors:
        identity_candidates = (
            waypoint
            for waypoint in map_waypoints
            if (
                int(waypoint.road_id),
                int(waypoint.section_id),
                int(waypoint.lane_id),
            )
            == point.identity
        )
        waypoint = min(
            identity_candidates,
            key=lambda candidate: hypot(
                float(candidate.transform.location.x) - point.x,
                float(candidate.transform.location.y) - point.y,
            ),
            default=None,
        )
        if waypoint is None:
            raise ExitRouteError("route point is absent from current map")
        corridor_distance = hypot(
            float(waypoint.transform.location.x) - point.x,
            float(waypoint.transform.location.y) - point.y,
        )
        if corridor_distance > point.lane_width_m:
            raise ExitRouteError("route point exceeds current lane corridor")
        if not bool(waypoint.lane_type & carla.LaneType.Driving):
            raise ExitRouteError("route point is not on a driving lane")
        if abs(float(waypoint.lane_width) - point.lane_width_m) > 0.25:
            raise ExitRouteError("route lane width does not match current map")
        waypoint_yaw = radians(float(waypoint.transform.rotation.yaw))
        if cos(waypoint_yaw - point.yaw_rad) <= cos(pi / 4.0):
            raise ExitRouteError("route tangent does not match current map")
        waypoints.append(waypoint)
    vehicle_width = float(vehicle.bounding_box.extent.y) * 2.0
    for point, waypoint in zip(envelope, waypoints[: len(envelope)], strict=True):
        if point.lane_id != route.source_lane_id:
            continue
        adjacent = waypoint.get_right_lane()
        if adjacent is None or int(adjacent.lane_id) != route.target_lane_id:
            raise ExitRouteError("route target is not the immediate right driving lane")
        source_yaw = radians(float(waypoint.transform.rotation.yaw))
        target_yaw = radians(float(adjacent.transform.rotation.yaw))
        if cos(source_yaw - target_yaw) <= cos(pi / 4.0):
            raise ExitRouteError("route source and target lane directions disagree")
        lane_change = waypoint.lane_change
        if not bool(lane_change & carla.LaneChange.Right):
            raise ExitRouteError(
                "route source lane does not permit a right lane change"
            )
        if vehicle_width >= min(float(waypoint.lane_width), float(adjacent.lane_width)):
            raise ExitRouteError("vehicle does not fit the route lane envelope")


def route_data_sha256(routes: tuple[ExitRoute, ...]) -> str:
    data = [
        {
            "route_id": route.route_id,
            "route_version": route.route_version,
            "map_name": route.map_name,
            "source_lane_id": route.source_lane_id,
            "target_lane_id": route.target_lane_id,
            "fork_distance_m": route.fork_distance_m,
            "initiation_start_m": route.initiation_start_m,
            "initiation_end_m": route.initiation_end_m,
            "completion_distance_m": route.completion_distance_m,
            "navigation_start_m": route.navigation_start_m,
            "navigation_end_m": route.navigation_end_m,
            "confirmation_start_m": route.confirmation_start_m,
            "confirmation_end_m": route.confirmation_end_m,
            "through_segments": route.through_segments,
            "exit_segments": route.exit_segments,
            "points": [
                [
                    point.x,
                    point.y,
                    point.yaw_rad,
                    point.distance_m,
                    point.road_id,
                    point.section_id,
                    point.lane_id,
                    point.lane_width_m,
                ]
                for point in route.points
            ],
        }
        for route in routes
    ]
    encoded = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_route(raw) -> ExitRoute:
    points = tuple(RoutePoint(*point) for point in raw["points"])
    return ExitRoute(
        route_id=raw["route_id"],
        route_version=raw["route_version"],
        map_name=raw["map_name"],
        source_lane_id=raw["source_lane_id"],
        target_lane_id=raw["target_lane_id"],
        fork_distance_m=raw["fork_distance_m"],
        points=points,
        through_segments=tuple(tuple(value) for value in raw["through_segments"]),
        exit_segments=tuple(tuple(value) for value in raw["exit_segments"]),
        initiation_start_m=raw.get("initiation_start_m", 0.0),
        initiation_end_m=raw.get("initiation_end_m"),
        completion_distance_m=raw.get("completion_distance_m"),
        navigation_start_m=raw.get("navigation_start_m"),
        navigation_end_m=raw.get("navigation_end_m"),
        confirmation_start_m=raw.get("confirmation_start_m"),
        confirmation_end_m=raw.get("confirmation_end_m"),
    )
