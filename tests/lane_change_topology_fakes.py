from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import carla

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.lane_change import LaneChangeDirection
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter
from tests.test_lane_change_execution_lifecycle import Rig, make_rig

LaneIdentity = tuple[int, int, int]


class TopologyWaypoint:
    def __init__(
        self,
        identity: LaneIdentity,
        transform: carla.Transform,
        *,
        is_junction: bool = False,
    ) -> None:
        self.road_id, self.section_id, self.lane_id = identity
        self.transform = transform
        self.lane_width = 3.5
        self.lane_type = carla.LaneType.Driving
        self.lane_change = carla.LaneChange.Both
        self.is_junction = is_junction
        self.left: TopologyWaypoint | None = None
        self.right: TopologyWaypoint | None = None

    @property
    def identity(self) -> LaneIdentity:
        return (self.road_id, self.section_id, self.lane_id)

    def get_left_lane(self) -> TopologyWaypoint | None:
        return self.left

    def get_right_lane(self) -> TopologyWaypoint | None:
        return self.right


class TopologyMap:
    def __init__(
        self,
        current: TopologyWaypoint | None,
        topology: Sequence[tuple[TopologyWaypoint, TopologyWaypoint]],
    ) -> None:
        self.current = current
        self.topology = list(topology)
        self.lookup_queue: list[TopologyWaypoint | None] = []
        self.lookup_count = 0

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> TopologyWaypoint | None:
        assert project_to_road is True
        assert lane_type == carla.LaneType.Driving
        self.lookup_count += 1
        if self.lookup_queue:
            return self.lookup_queue.pop(0)
        return self.current

    def get_topology(self) -> list[tuple[TopologyWaypoint, TopologyWaypoint]]:
        return list(self.topology)


@dataclass(frozen=True, slots=True)
class TopologyScenario:
    carla_map: TopologyMap
    source_path: tuple[TopologyWaypoint, ...]
    target_path: tuple[TopologyWaypoint, ...]

    @property
    def old_source(self) -> TopologyWaypoint:
        return self.source_path[0]

    @property
    def old_target(self) -> TopologyWaypoint:
        return self.target_path[0]

    @property
    def new_source(self) -> TopologyWaypoint:
        return self.source_path[-1]

    @property
    def new_target(self) -> TopologyWaypoint:
        return self.target_path[-1]


def topology_scenario(
    direction: LaneChangeDirection,
    segments: tuple[tuple[int, int], ...] = ((36, 0), (37, 0)),
) -> TopologyScenario:
    target_lane = -2 if direction is LaneChangeDirection.LEFT else -4
    target_y = -3.5 if direction is LaneChangeDirection.LEFT else 3.5
    sources = tuple(
        TopologyWaypoint(
            (road, section, -3),
            carla.Transform(carla.Location(x=10.0 + index)),
        )
        for index, (road, section) in enumerate(segments)
    )
    targets = tuple(
        TopologyWaypoint(
            (road, section, target_lane),
            carla.Transform(carla.Location(x=10.0 + index, y=target_y)),
        )
        for index, (road, section) in enumerate(segments)
    )
    for source, target in zip(sources, targets, strict=True):
        if direction is LaneChangeDirection.LEFT:
            source.left = target
            target.right = source
        else:
            source.right = target
            target.left = source
    topology = list(pairwise(sources))
    topology.extend(pairwise(targets))
    return TopologyScenario(TopologyMap(sources[0], topology), sources, targets)


def topology_rig(direction: LaneChangeDirection, scenario: TopologyScenario) -> Rig:
    rig = make_rig(
        direction,
        submit_general=False,
        source_road_id=scenario.old_source.road_id,
        source_section_id=scenario.old_source.section_id,
        target_road_id=scenario.old_target.road_id,
    )
    rig.observer._requests._adjacent_lanes = CarlaAdjacentLaneAdapter(
        scenario.carla_map
    )
    rig.observer.update(DriverInput(lane_change_direction=direction))
    return rig
