from math import isfinite

import carla
import pytest

from src.p7.carla_observation import observe
from src.p7.carla_traffic import CarlaTraffic
from src.p7.carla_visibility import projected_bounds
from src.p7.config import HazardConfig, P7Config
from src.p7.types import P7Error
from tests.p7_carla_fakes import World
from tests.test_p7_carla_boundary import backend, stamp


def reference_projection(
    box: carla.BoundingBox,
    actor_transform: carla.Transform,
    camera: carla.Transform,
) -> tuple[float, float, float, float]:
    inverse = camera.get_inverse_matrix()
    projected = []
    for vertex in box.get_world_vertices(actor_transform):
        vector = (vertex.x, vertex.y, vertex.z, 1)
        x, y, z = (
            sum(row[index] * vector[index] for index in range(4)) for row in inverse[:3]
        )
        assert x > 0 and all(isfinite(value) for value in (x, y, z))
        projected.append((y / x, z / x))
    return (
        min(point[0] for point in projected),
        min(point[1] for point in projected),
        max(point[0] for point in projected),
        max(point[1] for point in projected),
    )


def test_prepare_caches_each_hazard_local_box_once_for_repeated_observations():
    actors, ego, owned, _ = backend(World())

    actors.prepare(stamp())
    setup_reads = (
        ego.bounding_box_reads,
        actors.lead.bounding_box_reads,
        actors.cargo.bounding_box_reads,
    )
    observe(actors, stamp(1), driver=None, collision=None, foreign=())
    observe(actors, stamp(2), driver=None, collision=None, foreign=())

    assert setup_reads == (1, 1, 1)
    assert (
        ego.bounding_box_reads,
        actors.lead.bounding_box_reads,
        actors.cargo.bounding_box_reads,
    ) == setup_reads
    assert owned.close() == ()


def test_observe_reads_no_actor_local_boxes():
    actors, ego, owned, _ = backend(World())
    actors.prepare(stamp())
    ego.bounding_box_reads = 0
    actors.lead.bounding_box_reads = 0
    actors.cargo.bounding_box_reads = 0

    observe(actors, stamp(1), driver=None, collision=None, foreign=())

    assert (
        ego.bounding_box_reads,
        actors.lead.bounding_box_reads,
        actors.cargo.bounding_box_reads,
    ) == (0, 0, 0)
    assert owned.close() == ()


def test_traffic_prepare_reads_each_spawned_actor_local_box_once():
    actors, ego, owned, _ = backend(World())
    traffic = CarlaTraffic(actors, P7Config())

    traffic.prepare(stamp())

    assert [actor.bounding_box_reads for actor, _, _ in traffic.vehicles] == [1, 1]
    assert ego.mutations == []
    assert owned.close() == ()


def test_cached_local_offset_and_rotation_match_direct_world_vertex_formula():
    box = carla.BoundingBox(
        carla.Location(x=0.8, y=-0.3, z=0.4),
        carla.Vector3D(x=2.1, y=0.8, z=0.6),
    )
    box.rotation = carla.Rotation(pitch=4, yaw=17, roll=-3)
    actor_transform = carla.Transform(
        carla.Location(x=18, y=1.2, z=0.5),
        carla.Rotation(pitch=-2, yaw=11, roll=1),
    )
    camera = carla.Transform(carla.Location(z=1.3))

    actual = projected_bounds(box, camera, transform=actor_transform)

    assert (actual.left, actual.bottom, actual.right, actual.top) == pytest.approx(
        reference_projection(box, actor_transform, camera)
    )


def test_current_actor_movement_and_rotation_refresh_projection_and_clearance():
    actors, _, owned, _ = backend(World())
    actors.prepare(stamp())
    before = observe(actors, stamp(1), driver=None, collision=None, foreign=())
    actors.ego.transform = carla.Transform(
        carla.Location(x=3, y=0.2), carla.Rotation(yaw=8)
    )
    actors.lead.transform = carla.Transform(
        carla.Location(x=20, y=3.5), carla.Rotation(yaw=13)
    )

    after = observe(actors, stamp(2), driver=None, collision=None, foreign=())

    assert after.distance_m != pytest.approx(before.distance_m)
    assert after.lead_station_m != pytest.approx(before.lead_station_m)
    assert after.lead_lateral_m != pytest.approx(before.lead_lateral_m)
    assert after.reveal_clear is not before.reveal_clear
    assert owned.close() == ()


def test_failed_setup_does_not_publish_local_box_cache():
    actors, _, owned, _ = backend(World(fail_type=HazardConfig().lead_blueprint))

    with pytest.raises(P7Error, match="SPAWN_FAILED"):
        actors.prepare(stamp())

    assert actors.local_boxes is None
    assert owned.close() == ()


def test_actor_loss_does_not_refresh_or_use_cached_boxes():
    actors, ego, owned, _ = backend(World())
    actors.prepare(stamp())
    setup_reads = (
        ego.bounding_box_reads,
        actors.lead.bounding_box_reads,
        actors.cargo.bounding_box_reads,
    )
    actors.lead.is_alive = False

    observation = observe(actors, stamp(1), driver=None, collision=None, foreign=())

    assert not observation.actors_alive
    assert (
        ego.bounding_box_reads,
        actors.lead.bounding_box_reads,
        actors.cargo.bounding_box_reads,
    ) == setup_reads
    assert ego.mutations == []
    assert owned.close() == ()


def test_new_actor_manager_reads_fresh_local_boxes():
    first, _, first_owned, _ = backend(World())
    first.prepare(stamp())
    first_box = first.local_boxes.ego
    assert first_owned.close() == ()
    second, _, second_owned, _ = backend(World())

    second.prepare(stamp())

    assert second.local_boxes.ego is not first_box
    assert second.local_boxes.lead is not first.local_boxes.lead
    assert second.local_boxes.cargo is not first.local_boxes.cargo
    assert second_owned.close() == ()
