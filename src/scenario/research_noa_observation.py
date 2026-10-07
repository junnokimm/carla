from __future__ import annotations

import carla

from src.scenario.research_noa_types import ResearchNoAVehicle, ResearchNoAWorld


def current_lane_id(world: ResearchNoAWorld, hero: ResearchNoAVehicle) -> int | None:
    waypoint = world.get_map().get_waypoint(
        hero.get_transform().location,
        project_to_road=False,
        lane_type=carla.LaneType.Driving,
    )
    return None if waypoint is None else int(waypoint.lane_id)
