from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.lane_change import LaneChangeDirection
from src.vehicle.carla_lane_graph import (
    LaneGraph,
    LaneIdentity,
    build_lane_graph,
    identity,
)


class TopologyWaypoint(Protocol):
    road_id: int
    section_id: int
    lane_id: int
    lane_type: carla.LaneType
    is_junction: bool

    def get_left_lane(self) -> TopologyWaypoint | None: ...

    def get_right_lane(self) -> TopologyWaypoint | None: ...


class TopologyMap(Protocol):
    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool = True,
        lane_type: carla.LaneType = carla.LaneType.Driving,
    ) -> TopologyWaypoint | None: ...

    def get_topology(
        self,
    ) -> list[tuple[TopologyWaypoint, TopologyWaypoint]]: ...


@dataclass(frozen=True, slots=True)
class CarlaLanePairTopology:
    carla_map: TopologyMap
    direction: LaneChangeDirection
    source: LaneIdentity
    target: LaneIdentity
    graph: LaneGraph

    @property
    def identities(self) -> tuple[LaneIdentity, LaneIdentity]:
        return self.source, self.target

    def advance(
        self,
        transform: carla.Transform,
    ) -> tuple[LaneIdentity, LaneIdentity] | None:
        waypoint = self.carla_map.get_waypoint(
            transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        candidates = _observed_pairs(waypoint, self.direction)
        proven = {
            identities
            for pair in candidates
            if (identities := self._traverse_to((identity(pair[0]), identity(pair[1]))))
            is not None
        }
        return next(iter(proven)) if len(proven) == 1 else None

    def _traverse_to(
        self,
        observed: tuple[LaneIdentity, LaneIdentity],
    ) -> tuple[LaneIdentity, LaneIdentity] | None:
        current = self.identities
        visited: set[tuple[LaneIdentity, LaneIdentity]] = set()
        while True:
            if not self.graph.validates_pair(*current, self.direction):
                return None
            if current == observed:
                return current
            if current in visited:
                return None
            visited.add(current)
            source_successors = self.graph.successors_of(current[0])
            target_successors = self.graph.successors_of(current[1])
            if len(source_successors) != 1 or len(target_successors) != 1:
                return None
            following = (next(iter(source_successors)), next(iter(target_successors)))
            if self.graph.predecessors_of(following[0]) != frozenset((current[0],)):
                return None
            if self.graph.predecessors_of(following[1]) != frozenset((current[1],)):
                return None
            current = following


def capture_lane_pair_topology(
    carla_map: TopologyMap,
    transform: carla.Transform,
    direction: LaneChangeDirection,
) -> CarlaLanePairTopology | None:
    source = carla_map.get_waypoint(
        transform.location,
        project_to_road=True,
        lane_type=carla.LaneType.Driving,
    )
    pair = _lane_pair(source, direction)
    if pair is None:
        return None
    graph = build_lane_graph(carla_map.get_topology(), pair)
    topology = CarlaLanePairTopology(
        carla_map=carla_map,
        direction=direction,
        source=identity(pair[0]),
        target=identity(pair[1]),
        graph=graph,
    )
    if not graph.validates_pair(topology.source, topology.target, direction):
        return None
    return topology


def _lane_pair(
    source: TopologyWaypoint | None,
    direction: LaneChangeDirection,
) -> tuple[TopologyWaypoint, TopologyWaypoint] | None:
    if (
        source is None
        or source.is_junction
        or source.lane_type != carla.LaneType.Driving
    ):
        return None
    target = (
        source.get_left_lane()
        if direction is LaneChangeDirection.LEFT
        else source.get_right_lane()
    )
    if (
        target is None
        or target.is_junction
        or target.lane_type != carla.LaneType.Driving
    ):
        return None
    reverse = (
        target.get_right_lane()
        if direction is LaneChangeDirection.LEFT
        else target.get_left_lane()
    )
    if reverse is None or identity(reverse) != identity(source):
        return None
    return source, target


def _observed_pairs(
    waypoint: TopologyWaypoint | None,
    direction: LaneChangeDirection,
) -> tuple[tuple[TopologyWaypoint, TopologyWaypoint], ...]:
    if waypoint is None:
        return ()
    candidates: list[tuple[TopologyWaypoint, TopologyWaypoint]] = []
    source_pair = _lane_pair(waypoint, direction)
    if source_pair is not None:
        candidates.append(source_pair)
    source = (
        waypoint.get_right_lane()
        if direction is LaneChangeDirection.LEFT
        else waypoint.get_left_lane()
    )
    target_pair = _lane_pair(source, direction)
    if target_pair is not None and identity(target_pair[1]) == identity(waypoint):
        candidates.append(target_pair)
    return tuple(candidates)
