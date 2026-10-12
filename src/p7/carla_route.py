from __future__ import annotations

from dataclasses import dataclass
from math import atan2, ceil, cos, degrees, hypot, radians, sin

import carla

from src.experiment.exit_route import RoutePoint
from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.p7.geometry import station
from src.p7.types import P7Error


@dataclass(frozen=True, slots=True)
class CorridorError(P7Error):
    point_index: int
    station_m: float
    source_identity: tuple[int, int, int]
    target_identity: tuple[int, int, int] | None
    location: tuple[float, float, float]
    lane_change: str


def planar(transform: carla.Transform) -> PlanarPose:
    return PlanarPose(transform.location.x, transform.location.y, radians(transform.rotation.yaw))


@dataclass(frozen=True, slots=True)
class CarlaCorridor:
    points: tuple[RoutePoint, ...]
    source: tuple[carla.Transform, ...]
    target: tuple[carla.Transform, ...]
    target_lane_id: int | None

    @property
    def origin(self) -> PlanarPose:
        first = self.points[0]
        return PlanarPose(first.x, first.y, first.yaw_rad)

    @property
    def width(self) -> float:
        return self.points[0].lane_width_m

    def coordinates(self, transform: carla.Transform) -> tuple[float, float]:
        pose = planar(transform)
        return station(pose, self.origin), compute_lane_relative_geometry(pose, self.origin).lateral_error_m

    def transform_at(self, distance: float, *, lateral_fraction: float = 0, yaw_offset: float = 0) -> carla.Transform:
        if not 0 <= distance <= self.points[-1].distance_m:
            raise P7Error("ACTOR_PATH_EXHAUSTED")
        index = min(int(distance // 2), len(self.source) - 2)
        u = (distance - self.points[index].distance_m) / 2
        a, b = self.source[index], self.source[index + 1]
        x = a.location.x + u * (b.location.x - a.location.x)
        y = a.location.y + u * (b.location.y - a.location.y)
        z = a.location.z + u * (b.location.z - a.location.z)
        if lateral_fraction:
            if not self.target:
                raise P7Error("TARGET_LANE_UNAVAILABLE")
            c, d = self.target[index], self.target[index + 1]
            x += lateral_fraction * (c.location.x + u * (d.location.x - c.location.x) - x)
            y += lateral_fraction * (c.location.y + u * (d.location.y - c.location.y) - y)
            z += lateral_fraction * (c.location.z + u * (d.location.z - c.location.z) - z)
        yaw_delta = degrees(atan2(sin(radians(b.rotation.yaw - a.rotation.yaw)), cos(radians(b.rotation.yaw - a.rotation.yaw))))
        return carla.Transform(carla.Location(x=x, y=y, z=z),
                               carla.Rotation(pitch=a.rotation.pitch, yaw=a.rotation.yaw + u * yaw_delta + yaw_offset))


def build_corridor(start: carla.Waypoint, length_m: float, *, require_target: bool) -> CarlaCorridor:
    identity = (start.road_id, start.section_id, start.lane_id)
    origin = planar(start.transform)
    source, target, points = [], [], []
    target_id = None
    waypoint = start

    def failure(reason: str, adjacent: carla.Waypoint | None = None) -> CorridorError:
        location = waypoint.transform.location
        return CorridorError(reason, index, index * 2,
            (waypoint.road_id, waypoint.section_id, waypoint.lane_id),
            None if adjacent is None else (adjacent.road_id, adjacent.section_id, adjacent.lane_id),
            (location.x, location.y, location.z), str(waypoint.lane_change))

    for index in range(ceil(length_m / 2) + 1):
        if (waypoint.road_id, waypoint.section_id, waypoint.lane_id) != identity or waypoint.is_junction or waypoint.lane_type != carla.LaneType.Driving:
            raise failure("ROUTE_GEOMETRY_INCOMPATIBLE")
        geometry = compute_lane_relative_geometry(planar(waypoint.transform), origin)
        if abs(geometry.lateral_error_m) > 0.35 or abs(geometry.heading_error_rad) > radians(3) or abs(waypoint.transform.rotation.pitch) > 3:
            raise failure("DEV_CORRIDOR_REQUIRES_STRAIGHT_NEAR_LEVEL_ROAD")
        source.append(waypoint.transform)
        points.append(RoutePoint(waypoint.transform.location.x, waypoint.transform.location.y,
                                  radians(waypoint.transform.rotation.yaw), index * 2,
                                  waypoint.road_id, waypoint.section_id, waypoint.lane_id, waypoint.lane_width))
        if require_target:
            adjacent = waypoint.get_right_lane()
            if (adjacent is None or adjacent.lane_type != carla.LaneType.Driving or adjacent.is_junction
                    or adjacent.lane_id * waypoint.lane_id <= 0
                    or (adjacent.road_id, adjacent.section_id) != identity[:2]
                    or not int(waypoint.lane_change) & int(carla.LaneChange.Right)):
                raise failure("LEAD_PATH_IMPOSSIBLE_NO_PERMITTED_SAME_DIRECTION_RIGHT_LANE", adjacent)
            if target_id is not None and adjacent.lane_id != target_id:
                raise failure("TARGET_LANE_CHANGED_WITHIN_CORRIDOR", adjacent)
            if abs(compute_lane_relative_geometry(planar(adjacent.transform), origin).heading_error_rad) > radians(3):
                raise failure("TARGET_LANE_HEADING_INCOMPATIBLE", adjacent)
            target_id = adjacent.lane_id
            target.append(adjacent.transform)
        if index < ceil(length_m / 2):
            following = waypoint.next(2.0)
            if len(following) != 1:
                raise failure("ROUTE_BRANCH_OR_END:next(2m)")
            if hypot(following[0].transform.location.x - waypoint.transform.location.x,
                     following[0].transform.location.y - waypoint.transform.location.y) < 1.5:
                raise failure("DEGENERATE_ROUTE_STEP:next(2m)")
            waypoint = following[0]
    return CarlaCorridor(tuple(points), tuple(source), tuple(target), target_id)
