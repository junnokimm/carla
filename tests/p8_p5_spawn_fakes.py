from __future__ import annotations

from math import atan2, degrees

import carla

from src.experiment.exit_route import ExitRoute
from src.scenario.research_noa_config import ResearchNoARunConfig
from src.vehicle.carla_exit_route import load_exit_route_manifest
from tests.research_noa_fakes import FakeResearchWorld


def configure_route_spawn(
    world: FakeResearchWorld,
    route: ExitRoute,
    station_m: float,
) -> None:
    segment_index = next(
        index
        for index, point in enumerate(route.points[:-1])
        if point.distance_m >= station_m
    )
    start = route.points[segment_index]
    end = route.points[segment_index + 1]
    yaw = degrees(atan2(end.y - start.y, end.x - start.x))
    transform = carla.Transform(
        carla.Location(
            x=(start.x + end.x) / 2.0,
            y=(start.y + end.y) / 2.0,
        ),
        carla.Rotation(yaw=yaw),
    )
    world.map.name = route.map_name
    world.map.spawn_point = transform
    world.vehicle.transform = transform
    world.map.waypoint.transform = transform
    world.map.waypoint.road_id = start.road_id
    world.map.waypoint.section_id = start.section_id
    world.map.waypoint.lane_id = start.lane_id
    world.map.waypoint.lane_width = start.lane_width_m


def configure_p5_spawn(
    world: FakeResearchWorld,
    config: ResearchNoARunConfig,
) -> None:
    assert config.exit_assistance is not None
    route = load_exit_route_manifest(config.exit_assistance.manifest_path).route(
        config.exit_assistance.route_id
    )
    configure_route_spawn(world, route, route.navigation_start)
