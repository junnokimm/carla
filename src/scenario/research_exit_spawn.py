from __future__ import annotations

from math import cos, pi, radians

import carla

from src.experiment.exit_route import ExitRoute, ExitRouteError, project_route
from src.experiment.lane_geometry import PlanarPose
from src.experiment.noa_runtime import CarlaNoARuntimeWaypoint


def validate_exit_spawn(
    route: ExitRoute,
    transform: carla.Transform,
    waypoint: CarlaNoARuntimeWaypoint,
) -> None:
    pose = PlanarPose(
        float(transform.location.x),
        float(transform.location.y),
        radians(float(transform.rotation.yaw)),
    )
    corridor_m = (
        min(
            float(waypoint.lane_width),
            *(point.lane_width_m for point in route.points),
        )
        / 2.0
    )
    projection = project_route(route, pose, corridor_m=corridor_m)
    if cos(projection.heading_error_rad) <= cos(pi / 4.0):
        raise ExitRouteError("spawn direction disagrees with exit route")
    # Allow approach before t0 so Module 1 can voluntarily activate NoA first.
    if (
        not route.points[0].distance_m
        <= projection.distance_m
        <= route.confirmation_end
    ):
        raise ExitRouteError("spawn is outside the exit guidance window")
    route_point = route.points[projection.point_index]
    waypoint_identity = (
        int(waypoint.road_id),
        int(waypoint.section_id),
        int(waypoint.lane_id),
    )
    if waypoint_identity != route_point.identity or (
        waypoint_identity[2] != route.source_lane_id
    ):
        raise ExitRouteError("spawn lane identity does not match exit route approach")
