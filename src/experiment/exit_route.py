from __future__ import annotations

from dataclasses import dataclass
from math import atan2, hypot, isfinite

from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry


@dataclass(frozen=True, slots=True)
class RoutePoint:
    x: float
    y: float
    yaw_rad: float
    distance_m: float
    road_id: int
    section_id: int
    lane_id: int
    lane_width_m: float

    @property
    def identity(self) -> tuple[int, int, int]:
        return self.road_id, self.section_id, self.lane_id


@dataclass(frozen=True, slots=True)
class ExitRoute:
    route_id: str
    route_version: str
    map_name: str
    source_lane_id: int
    target_lane_id: int
    fork_distance_m: float
    points: tuple[RoutePoint, ...]
    through_segments: tuple[tuple[int, int, int], ...]
    exit_segments: tuple[tuple[int, int, int], ...]
    initiation_start_m: float = 0.0
    initiation_end_m: float | None = None
    completion_distance_m: float | None = None
    navigation_start_m: float | None = None
    navigation_end_m: float | None = None
    confirmation_start_m: float | None = None
    confirmation_end_m: float | None = None

    def __post_init__(self) -> None:
        if not self.route_id.strip() or not self.route_version.strip():
            raise ExitRouteError("route identity must not be blank")
        if len(self.points) < 2:
            raise ExitRouteError("route requires at least two points")
        previous = -1.0
        for point in self.points:
            values = (
                point.x,
                point.y,
                point.yaw_rad,
                point.distance_m,
                point.lane_width_m,
            )
            if not all(isfinite(value) for value in values):
                raise ExitRouteError("route point values must be finite")
            if point.distance_m <= previous:
                raise ExitRouteError("route distances must increase")
            previous = point.distance_m
        initiation_end = (
            self.fork_distance_m
            if self.initiation_end_m is None
            else self.initiation_end_m
        )
        completion = (
            self.points[-1].distance_m
            if self.completion_distance_m is None
            else self.completion_distance_m
        )
        navigation_start = (
            self.initiation_start_m
            if self.navigation_start_m is None
            else self.navigation_start_m
        )
        navigation_end = (
            initiation_end if self.navigation_end_m is None else self.navigation_end_m
        )
        confirmation_start = (
            navigation_start
            if self.confirmation_start_m is None
            else self.confirmation_start_m
        )
        confirmation_end = (
            initiation_end
            if self.confirmation_end_m is None
            else self.confirmation_end_m
        )
        if not (
            0.0 <= self.initiation_start_m < initiation_end <= self.fork_distance_m
        ):
            raise ExitRouteError("route initiation envelope is invalid")
        if not self.fork_distance_m <= completion <= self.points[-1].distance_m:
            raise ExitRouteError("route completion distance is invalid")
        if not (
            self.points[0].distance_m
            <= navigation_start
            <= confirmation_start
            <= confirmation_end
            <= navigation_end
            <= self.fork_distance_m
            and self.initiation_start_m < initiation_end <= navigation_end
        ):
            raise ExitRouteError("route guidance envelopes are invalid")
        if (
            self.confirmation_end_m is not None
            and confirmation_end > self.initiation_start_m
        ):
            raise ExitRouteError("route confirmation must close before maneuver onset")
        lane_ids = {point.lane_id for point in self.points}
        if self.source_lane_id not in lane_ids or self.target_lane_id not in lane_ids:
            raise ExitRouteError("route points must contain source and target lanes")

    @property
    def navigation_start(self) -> float:
        return (
            self.initiation_start_m
            if self.navigation_start_m is None
            else self.navigation_start_m
        )

    @property
    def navigation_end(self) -> float:
        if self.navigation_end_m is not None:
            return self.navigation_end_m
        return (
            self.fork_distance_m
            if self.initiation_end_m is None
            else self.initiation_end_m
        )

    @property
    def confirmation_start(self) -> float:
        return (
            self.navigation_start
            if self.confirmation_start_m is None
            else self.confirmation_start_m
        )

    @property
    def confirmation_end(self) -> float:
        if self.confirmation_end_m is not None:
            return self.confirmation_end_m
        return (
            self.fork_distance_m
            if self.initiation_end_m is None
            else self.initiation_end_m
        )


@dataclass(frozen=True, slots=True)
class RouteProjection:
    distance_m: float
    lateral_offset_m: float
    heading_error_rad: float
    reference_pose: PlanarPose
    point_index: int
    distance_from_route_m: float


class ExitRouteError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


def project_route(
    route: ExitRoute,
    vehicle_pose: PlanarPose,
    *,
    cursor: RouteProjection | None = None,
    start_index: int = 0,
    corridor_m: float | None = None,
) -> RouteProjection:
    best_squared = float("inf")
    best: RouteProjection | None = None
    search_start = start_index if cursor is None else cursor.point_index
    for index in range(search_start, len(route.points) - 1):
        start = route.points[index]
        end = route.points[index + 1]
        delta_x = end.x - start.x
        delta_y = end.y - start.y
        squared_length = delta_x * delta_x + delta_y * delta_y
        if squared_length == 0.0:
            continue
        fraction = min(
            1.0,
            max(
                0.0,
                (
                    (vehicle_pose.x - start.x) * delta_x
                    + (vehicle_pose.y - start.y) * delta_y
                )
                / squared_length,
            ),
        )
        reference = PlanarPose(
            start.x + fraction * delta_x,
            start.y + fraction * delta_y,
            atan2(delta_y, delta_x),
        )
        squared_distance = (vehicle_pose.x - reference.x) ** 2 + (
            vehicle_pose.y - reference.y
        ) ** 2
        if squared_distance >= best_squared:
            continue
        geometry = compute_lane_relative_geometry(vehicle_pose, reference)
        best_squared = squared_distance
        best = RouteProjection(
            distance_m=start.distance_m + fraction * hypot(delta_x, delta_y),
            lateral_offset_m=geometry.lateral_error_m,
            heading_error_rad=geometry.heading_error_rad,
            reference_pose=reference,
            point_index=index,
            distance_from_route_m=hypot(
                vehicle_pose.x - reference.x,
                vehicle_pose.y - reference.y,
            ),
        )
    if best is None:
        raise ExitRouteError("route has no projectable segment")
    if cursor is not None and best.distance_m < cursor.distance_m:
        raise ExitRouteError("route projection regressed behind cursor")
    if corridor_m is not None and best.distance_from_route_m > corridor_m:
        raise ExitRouteError("vehicle is outside route projection corridor")
    return best
