from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.experiment.lane_change import LaneChangeDirection

LaneIdentity = tuple[int, int, int]


class LaneGraphWaypoint(Protocol):
    road_id: int
    section_id: int
    lane_id: int
    is_junction: bool

    def get_left_lane(self) -> LaneGraphWaypoint | None: ...

    def get_right_lane(self) -> LaneGraphWaypoint | None: ...


@dataclass(frozen=True, slots=True)
class _Node:
    identity: LaneIdentity
    is_junction: bool
    left: frozenset[LaneIdentity | None]
    right: frozenset[LaneIdentity | None]


@dataclass(frozen=True, slots=True)
class LaneGraph:
    successors: tuple[tuple[LaneIdentity, frozenset[LaneIdentity]], ...]
    predecessors: tuple[tuple[LaneIdentity, frozenset[LaneIdentity]], ...]
    nodes: tuple[_Node, ...]

    def successors_of(self, identity: LaneIdentity) -> frozenset[LaneIdentity]:
        return _lookup_edges(self.successors, identity)

    def predecessors_of(self, identity: LaneIdentity) -> frozenset[LaneIdentity]:
        return _lookup_edges(self.predecessors, identity)

    def validates_pair(
        self,
        source: LaneIdentity,
        target: LaneIdentity,
        direction: LaneChangeDirection,
    ) -> bool:
        source_node = self._node(source)
        target_node = self._node(target)
        if source_node is None or target_node is None:
            return False
        if source_node.is_junction or target_node.is_junction:
            return False
        adjacent = (
            source_node.left
            if direction is LaneChangeDirection.LEFT
            else source_node.right
        )
        reverse = (
            target_node.right
            if direction is LaneChangeDirection.LEFT
            else target_node.left
        )
        return adjacent == frozenset((target,)) and reverse == frozenset((source,))

    def _node(self, identity: LaneIdentity) -> _Node | None:
        return next((node for node in self.nodes if node.identity == identity), None)


def build_lane_graph(
    edges: list[tuple[LaneGraphWaypoint, LaneGraphWaypoint]],
    captured_pair: tuple[LaneGraphWaypoint, LaneGraphWaypoint],
) -> LaneGraph:
    successors: dict[LaneIdentity, set[LaneIdentity]] = {}
    predecessors: dict[LaneIdentity, set[LaneIdentity]] = {}
    samples: dict[LaneIdentity, list[LaneGraphWaypoint]] = {}
    for start, end in edges:
        start_id = identity(start)
        end_id = identity(end)
        samples.setdefault(start_id, []).append(start)
        samples.setdefault(end_id, []).append(end)
        if start_id == end_id:
            continue
        successors.setdefault(start_id, set()).add(end_id)
        predecessors.setdefault(end_id, set()).add(start_id)
    for waypoint in captured_pair:
        samples.setdefault(identity(waypoint), []).append(waypoint)
    return LaneGraph(
        successors=_freeze_edges(successors),
        predecessors=_freeze_edges(predecessors),
        nodes=tuple(
            _node(node_id, waypoints) for node_id, waypoints in samples.items()
        ),
    )


def identity(waypoint: LaneGraphWaypoint) -> LaneIdentity:
    return waypoint.road_id, waypoint.section_id, waypoint.lane_id


def _node(identity_value: LaneIdentity, samples: list[LaneGraphWaypoint]) -> _Node:
    return _Node(
        identity=identity_value,
        is_junction=any(waypoint.is_junction for waypoint in samples),
        left=frozenset(
            _optional_identity(waypoint.get_left_lane()) for waypoint in samples
        ),
        right=frozenset(
            _optional_identity(waypoint.get_right_lane()) for waypoint in samples
        ),
    )


def _optional_identity(waypoint: LaneGraphWaypoint | None) -> LaneIdentity | None:
    return None if waypoint is None else identity(waypoint)


def _freeze_edges(
    edges: dict[LaneIdentity, set[LaneIdentity]],
) -> tuple[tuple[LaneIdentity, frozenset[LaneIdentity]], ...]:
    return tuple(
        (identity_value, frozenset(adjacent))
        for identity_value, adjacent in edges.items()
    )


def _lookup_edges(
    edges: tuple[tuple[LaneIdentity, frozenset[LaneIdentity]], ...],
    identity_value: LaneIdentity,
) -> frozenset[LaneIdentity]:
    return next(
        (adjacent for node, adjacent in edges if node == identity_value),
        frozenset(),
    )
