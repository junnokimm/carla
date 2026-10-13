from __future__ import annotations

from pathlib import Path

import carla
import pytest

from src.experiment.lane_change import LaneChangeDirection
from src.vehicle.carla_lane_topology import capture_lane_pair_topology


@pytest.fixture(scope="module")
def highway_map() -> carla.Map:
    xodr = Path("maps/highway_loop_3lane.xodr").read_text(encoding="utf-8")
    return carla.Map("highway_loop_3lane", xodr)


def identity(waypoint: carla.Waypoint) -> tuple[int, int, int]:
    return (waypoint.road_id, waypoint.section_id, waypoint.lane_id)


def test_serverless_map_exposes_expected_exact_topology(highway_map: carla.Map) -> None:
    edges = {
        (identity(start), identity(end)) for start, end in highway_map.get_topology()
    }

    assert len(edges) == 24
    assert ((1, 0, -3), (2, 0, -3)) in edges
    assert ((2, 0, -3), (3, 0, -3)) in edges
    assert ((1, 0, 3), (4, 0, 3)) in edges


@pytest.mark.parametrize(
    ("direction", "target_lane"),
    [(LaneChangeDirection.LEFT, -2), (LaneChangeDirection.RIGHT, -4)],
)
def test_serverless_map_proves_curved_multi_road_lane_pair_path(
    highway_map: carla.Map,
    direction: LaneChangeDirection,
    target_lane: int,
) -> None:
    source = highway_map.get_waypoint_xodr(1, -3, 10.0)
    observed = highway_map.get_waypoint_xodr(3, -3, 10.0)
    assert source is not None
    assert observed is not None
    topology = capture_lane_pair_topology(highway_map, source.transform, direction)
    assert topology is not None

    identities = topology.advance(observed.transform)

    assert identities == ((3, 0, -3), (3, 0, target_lane))
