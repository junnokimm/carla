from itertools import pairwise
from types import SimpleNamespace

import carla
import pytest

from src.p7.carla_route import build_corridor
from src.p7.live import catalog
from src.p7.types import P7Error
from tests.p7_carla_fakes import Actor, Blueprint, Waypoint, World


def invalid_chain():
    points = [Waypoint(i * 2) for i in range(6)]
    for left, right in pairwise(points):
        left.next = lambda distance, target=right: [target]
    points[3].lane_change = carla.LaneChange.Left
    return points[0]


def test_same_live_guard_reports_first_failed_station():
    with pytest.raises(P7Error) as caught:
        build_corridor(invalid_chain(), 10, require_target=True)

    assert caught.value.point_index == 3
    assert caught.value.station_m == 6
    assert caught.value.source_identity == (1, 0, -1)
    assert "RIGHT_LANE" in caught.value.reason


def test_explicit_ego_catalog_only_reads_and_never_repositions(monkeypatch):
    world, ego = World(), Actor(71)
    queried = []
    world.get_actor = lambda identifier: queried.append(identifier) or (ego if identifier == 71 else None)
    world.get_actors = lambda: SimpleNamespace(filter=lambda pattern: [ego])
    world.filter = lambda pattern: [Blueprint("static.prop.box01")]
    world_map = world.get_map()
    world_map.get_waypoint = lambda *args, **kwargs: invalid_chain()
    world.get_map = lambda: world_map
    monkeypatch.setattr("src.p7.live.carla.Client", lambda host, port: SimpleNamespace(
        set_timeout=lambda timeout: None, get_world=lambda: world))

    report = catalog("fake", 1, ego_id=71, corridor_length_m=10)

    assert queried == [71]
    assert report["corridor"]["status"] == "INCOMPATIBLE"
    assert report["corridor"]["first_failure"]["station_m"] == 6
    assert report["corridor"]["route_approved"] is False
    assert ego.mutations == [] and world.spawned == []

    catalog("fake", 1)
    assert queried == [71]
