from dataclasses import replace

import carla
import pytest

from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.p7.carla_actors import CarlaHazardActors
from src.p7.carla_observation import observe
from src.p7.carla_route import build_corridor
from src.p7.carla_traffic import CarlaTraffic
from src.p7.config import HazardConfig, P7Config
from src.p7.ownership import OwnedActors
from src.p7.types import P7Error
from tests.p7_carla_fakes import Actor, Waypoint, World


class TrafficMap:
    def __init__(self, spawn_count, first_valid_index=None):
        self.spawn_points = [
            carla.Transform(carla.Location(x=100 + index * 30))
            for index in range(spawn_count)
        ]
        self.first_valid_x = (
            None
            if first_valid_index is None
            else self.spawn_points[first_valid_index].location.x
        )

    def get_spawn_points(self):
        return self.spawn_points

    def get_waypoint(self, location, **kwargs):
        if self.first_valid_x is None or location.x < self.first_valid_x:
            return None
        return Waypoint(location.x, location.y)


def stamp(second=0):
    ns = int(second * 1e9)
    return TimestampEnvelope(HostClockTimestamp(ns, ns), CarlaSnapshotTimestamp(second, int(second * 10), ns, ns))


def backend(world):
    ego = Actor(1)
    owned = OwnedActors(ego.id)
    events = []
    actors = CarlaHazardActors(world, ego, build_corridor(Waypoint(), 90, require_target=True),
                               config=HazardConfig(), owned=owned,
                               emit=lambda *event: events.append(event), run_id="fake-boundary")
    return actors, ego, owned, events


def test_lead_motion_does_not_mutate_ego_or_stationary_cargo():
    actors, ego, owned, _ = backend(World())
    actors.prepare(stamp())
    cargo_mutations = list(actors.cargo.mutations)

    actors.advance(0)
    actors.begin_reveal(stamp(1))
    actors.advance(2.5)
    midway = actors.lead.get_location()
    actors.advance(4)

    assert midway.y == pytest.approx(1.75)
    assert actors.lead.get_location().y == pytest.approx(3.5)
    assert actors.complete
    assert actors.obstacle_stationary
    assert actors.cargo.mutations == cargo_mutations
    assert ego.mutations == []
    assert owned.close() == ()
    assert ego.is_alive


def test_missing_right_lane_blocks_before_spawn():
    with pytest.raises(P7Error, match="LEAD_PATH_IMPOSSIBLE"):
        build_corridor(Waypoint(right=False), 90, require_target=True)


def test_missing_obstacle_asset_has_no_substitute_or_partial_spawn():
    world = World(missing_type="static.prop.box01")
    actors, ego, _, _ = backend(world)

    with pytest.raises(P7Error, match="BLUEPRINT_UNAVAILABLE"):
        actors.prepare(stamp())

    assert world.spawned == []
    assert ego.mutations == []


def test_lead_spawn_failure_preserves_owned_cargo_for_cleanup():
    world = World(fail_type=HazardConfig().lead_blueprint)
    actors, ego, owned, events = backend(world)

    with pytest.raises(P7Error, match="SPAWN_FAILED"):
        actors.prepare(stamp())
    assert owned.close() == ()

    assert len(world.spawned) == 1
    assert not world.spawned[0].is_alive
    assert ego.mutations == []
    assert any(event[0].value == "p7_actor_spawn_failure" for event in events)


def test_alias_ego_is_rejected_before_any_mutation():
    ego = Actor(1)
    actors, _, owned, _ = backend(World(alias=ego))

    with pytest.raises(P7Error, match="EGO"):
        actors.prepare(stamp())

    assert owned.close() == ()
    assert ego.mutations == []


def test_obstacle_drift_and_actor_loss_are_detected():
    actors, _, owned, _ = backend(World())
    actors.prepare(stamp())
    actors.cargo.set_transform(carla.Transform(carla.Location(x=99)))
    assert not actors.obstacle_stationary
    actors.lead.is_alive = False

    with pytest.raises(P7Error, match="ACTOR_LOST"):
        actors.advance(1)
    owned.close()


def test_generic_traffic_stays_outside_reserved_hazard_path():
    actors, ego, owned, _ = backend(World())
    traffic = CarlaTraffic(actors, P7Config())
    traffic.prepare(stamp())

    traffic.advance(0, (ego,))
    traffic.advance(10, (ego,))

    assert len(traffic.vehicles) == 2
    assert all(actor.get_location().x < -25 for actor, _, _ in traffic.vehicles)
    assert ego.mutations == []
    assert owned.close() == ()


def test_traffic_prepare_scans_late_candidates_after_first_128_are_rejected():
    world = World()
    world.get_map = lambda: TrafficMap(130, first_valid_index=128)
    actors, ego, owned, events = backend(world)
    traffic = CarlaTraffic(actors, P7Config())

    traffic.prepare(stamp())

    scan = next(
        event[2] for event in events if event[0].value == "traffic_candidate_scan"
    )
    assert scan == {
        "total_spawn_count": 130,
        "inspected_count": 130,
        "valid_candidate_count": 2,
        "requested_count": 2,
    }
    assert len(traffic.vehicles) == 2
    assert {candidate[0].id for candidate in traffic.vehicles} == {100, 101}
    assert ego.mutations == []
    assert owned.close() == ()


def test_traffic_prepare_logs_scan_counts_and_spawns_nothing_when_insufficient():
    world = World()
    world.get_map = lambda: TrafficMap(130)
    actors, ego, owned, events = backend(world)
    traffic = CarlaTraffic(actors, P7Config())

    with pytest.raises(P7Error, match="INSUFFICIENT_CLEAR_TRAFFIC_CANDIDATES"):
        traffic.prepare(stamp())

    scan = next(
        event[2] for event in events if event[0].value == "traffic_candidate_scan"
    )
    assert scan == {
        "total_spawn_count": 130,
        "inspected_count": 130,
        "valid_candidate_count": 0,
        "requested_count": 2,
    }
    assert world.spawned == []
    assert traffic.vehicles == []
    assert ego.mutations == []
    assert owned.close() == ()


def test_invalid_dynamic_trigger_source_is_rejected():
    with pytest.raises(P7Error):
        replace(HazardConfig(), trigger_source="GAZE")


def test_clearance_uses_world_bounding_boxes_including_local_center_offsets():
    actors, ego, owned, _ = backend(World())
    ego._bounding_box.location.x = 1
    world = actors.world
    original_spawn = world.try_spawn_actor

    def spawn_with_offset(blueprint, transform):
        actor = original_spawn(blueprint, transform)
        if actor is not None and actor.type_id.startswith("static."):
            actor._bounding_box.location.x = 0.5
        return actor

    world.try_spawn_actor = spawn_with_offset
    actors.prepare(stamp())

    result = observe(actors, stamp(1), driver=None, collision=None, foreign=())

    assert result.distance_m == pytest.approx(42.05)
    assert owned.close() == ()
