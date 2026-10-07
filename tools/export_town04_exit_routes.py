from __future__ import annotations

import argparse
import json
from dataclasses import astuple, dataclass
from math import hypot, radians
from pathlib import Path
from typing import Final

import carla

from src.experiment.exit_route import ExitRoute, RoutePoint
from src.vehicle.carla_exit_route import route_data_sha256

OUTPUT: Final = Path("config/town04_exit_routes_dev_v3.json")
STEP_M: Final = 5.0
UPSTREAM_M: Final = 1000.0
DOWNSTREAM_M: Final = 300.0
ROUTES: Final = (
    (
        "town04-exit-39",
        39,
        1191,
        ((1184, 0, -3), (1184, 0, -4), (40, 0, -4)),
        ((1191, 1, -2), (33, 0, 2)),
    ),
    (
        "town04-exit-47",
        47,
        782,
        ((774, 0, -3), (774, 0, -4), (48, 0, -4)),
        ((782, 1, -2), (34, 0, 2)),
    ),
)
TOPOLOGY_LINKS: Final = {
    "town04-exit-39": {
        "through": (
            (1184, 0, -3),
            (39, 0, -4),
            (1184, 0, -4),
            (40, 0, -4),
        ),
        "exit": ((39, 0, -4), (1191, 0, -4), (1191, 1, -2), (33, 0, 2)),
    },
    "town04-exit-47": {
        "through": (
            (774, 0, -3),
            (47, 0, -4),
            (774, 0, -4),
            (48, 0, -4),
        ),
        "exit": ((47, 0, -4), (782, 0, -4), (782, 1, -2), (34, 0, 2)),
    },
}


class RouteExportError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SamplePoint:
    x: float
    y: float
    yaw_rad: float
    road_id: int
    section_id: int
    lane_id: int
    lane_width_m: float


def waypoint_sample(waypoint: carla.Waypoint) -> SamplePoint:
    transform = waypoint.transform
    return SamplePoint(
        float(transform.location.x),
        float(transform.location.y),
        radians(float(transform.rotation.yaw)),
        int(waypoint.road_id),
        int(waypoint.section_id),
        int(waypoint.lane_id),
        float(waypoint.lane_width),
    )


def choose(
    candidates: list[carla.Waypoint], preferred_road: int | None, target_lane: int
) -> carla.Waypoint:
    if preferred_road is not None:
        for candidate in candidates:
            if int(candidate.road_id) == preferred_road:
                return candidate
    return min(
        candidates,
        key=lambda item: (abs(int(item.lane_id) - target_lane), int(item.road_id)),
    )


def sample_route(
    carla_map: carla.Map,
    route_id: str,
    fork_road: int,
    exit_road: int,
    through: tuple[tuple[int, int, int], ...],
    exits: tuple[tuple[int, int, int], ...],
) -> ExitRoute:
    source_start = carla_map.get_waypoint_xodr(fork_road, -3, 0.0)
    if source_start is None:
        raise RouteExportError(f"missing fork seed for road {fork_road}")
    upstream = [source_start]
    current = source_start
    for _ in range(round(UPSTREAM_M / STEP_M)):
        previous = current.previous(STEP_M)
        if not previous:
            break
        current = choose(previous, None, -3)
        upstream.append(current)
    ordered = [waypoint_sample(waypoint) for waypoint in reversed(upstream)]
    target_end = 0
    while carla_map.get_waypoint_xodr(fork_road, -4, float(target_end + 1)) is not None:
        target_end += 1
    transition_start = max(5, target_end - 100)
    transition_end = target_end - 25
    initiation_waypoint = carla_map.get_waypoint_xodr(
        fork_road, -3, float(transition_start)
    )
    if initiation_waypoint is None:
        raise RouteExportError(f"missing initiation point on road {fork_road}")
    initiation_sample = waypoint_sample(initiation_waypoint)
    initiation_end_waypoint = carla_map.get_waypoint_xodr(
        fork_road, -4, float(transition_end)
    )
    if initiation_end_waypoint is None:
        raise RouteExportError(f"missing initiation end on road {fork_road}")
    initiation_end_sample = waypoint_sample(initiation_end_waypoint)
    source_distances = [*range(5, transition_start, 5), transition_start]
    for distance in source_distances:
        waypoint = carla_map.get_waypoint_xodr(fork_road, -3, float(distance))
        if waypoint is not None:
            ordered.append(waypoint_sample(waypoint))
    for distance in range(transition_start + 5, transition_end + 1, 5):
        source = carla_map.get_waypoint_xodr(fork_road, -3, float(distance))
        target = carla_map.get_waypoint_xodr(fork_road, -4, float(distance))
        if source is None or target is None:
            raise RouteExportError(f"missing lane-change envelope on road {fork_road}")
        fraction = (distance - transition_start) / (transition_end - transition_start)
        source_sample = waypoint_sample(source)
        target_sample = waypoint_sample(target)
        ordered.append(
            SamplePoint(
                source_sample.x + fraction * (target_sample.x - source_sample.x),
                source_sample.y + fraction * (target_sample.y - source_sample.y),
                source_sample.yaw_rad
                + fraction * (target_sample.yaw_rad - source_sample.yaw_rad),
                fork_road,
                int(source.section_id),
                -3 if fraction < 0.5 else -4,
                min(source_sample.lane_width_m, target_sample.lane_width_m),
            )
        )
    for distance in range(transition_end + 5, target_end + 1, 5):
        waypoint = carla_map.get_waypoint_xodr(fork_road, -4, float(distance))
        if waypoint is not None:
            ordered.append(waypoint_sample(waypoint))
    current = carla_map.get_waypoint_xodr(fork_road, -4, float(target_end))
    if current is None:
        raise RouteExportError(f"missing target fork on road {fork_road}")
    preferred = fork_road
    for _ in range(round(DOWNSTREAM_M / STEP_M)):
        following = current.next(STEP_M)
        if not following:
            break
        if len(following) > 1:
            preferred = exit_road
        current = choose(following, preferred, -4)
        ordered.append(waypoint_sample(current))
    points: list[RoutePoint] = []
    distance = 0.0
    previous_location = None
    for sample in ordered:
        if previous_location is not None:
            distance += hypot(
                sample.x - previous_location[0], sample.y - previous_location[1]
            )
        points.append(
            RoutePoint(
                round(sample.x, 6),
                round(sample.y, 6),
                round(sample.yaw_rad, 9),
                round(distance, 6),
                sample.road_id,
                sample.section_id,
                sample.lane_id,
                round(sample.lane_width_m, 6),
            )
        )
        previous_location = (sample.x, sample.y)
    fork_distance = next(
        point.distance_m
        for point in reversed(points)
        if point.road_id == fork_road and point.lane_id == -4
    )
    maneuver_start = min(
        points,
        key=lambda point: hypot(
            point.x - initiation_sample.x, point.y - initiation_sample.y
        ),
    ).distance_m
    maneuver_end = min(
        points,
        key=lambda point: hypot(
            point.x - initiation_end_sample.x,
            point.y - initiation_end_sample.y,
        ),
    ).distance_m
    navigation_start = max(points[0].distance_m, fork_distance - 1000.0)
    return ExitRoute(
        route_id,
        "town04-exits-dev-v3",
        "Carla/Maps/Town04",
        -3,
        -4,
        fork_distance,
        tuple(points),
        through,
        exits,
        maneuver_start,
        maneuver_end,
        points[-1].distance_m,
        navigation_start,
        fork_distance,
        navigation_start,
        maneuver_start,
    )


def main(output: Path | None = None) -> None:
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(3.0)
    carla_map = client.get_world().get_map()
    routes = tuple(sample_route(carla_map, *definition) for definition in ROUTES)
    ordered_links = []
    for route in routes:
        links: list[list[int]] = []
        for point in route.points:
            identity = [point.road_id, point.section_id, point.lane_id]
            if not links or links[-1] != identity:
                links.append(identity)
        ordered_links.append(links)
    payload = {
        "manifest_version": "town04-exits-dev-v3",
        "map_name": carla_map.name,
        "development_values_not_policy": True,
        "validation_criteria": {
            "maximum_sample_spacing_factor": 1.25,
            "maximum_tangent_delta_rad": 0.2,
            "lane_change_direction": "RIGHT",
            "requires_immediate_adjacency": True,
            "requires_same_direction": True,
            "requires_vehicle_fit": True,
        },
        "sample_step_m": STEP_M,
        "navigation_trigger_distance_m": 1000.0,
        "response_timeout_s": 5.0,
        "source_lane_id": -3,
        "target_lane_id": -4,
        "route_data_sha256": route_data_sha256(routes),
        "routes": [
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
                "ordered_links": ordered_links[index],
                "topology_links": TOPOLOGY_LINKS[route.route_id],
                "points": [list(astuple(point)) for point in route.points],
            }
            for index, route in enumerate(routes)
        ],
    }
    output_path = OUTPUT if output is None else output
    with output_path.open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(payload, indent=2) + "\n")
    print(
        json.dumps(
            {
                "path": str(output_path),
                "hash": payload["route_data_sha256"],
                "points": [len(route.points) for route in routes],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    main(arguments.output)
