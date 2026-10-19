"""Collision evidence survives while the requested M2 drive continues."""

from types import SimpleNamespace

import carla
import pytest

from tests.test_m2_hazard_v1 import scene_fixture, tick


def collision(world, identifier=0):
    return SimpleNamespace(
        other_actor=SimpleNamespace(
            id=identifier, type_id="static.test_barrier", semantic_tags=[1, 2]
        ),
        actor=world.vehicle,
        normal_impulse=carla.Vector3D(3, 4, 0),
        transform=carla.Transform(carla.Location(x=23)),
        frame=world.frame,
        timestamp=world.simulation_seconds,
    )


@pytest.mark.parametrize("identifier", [0, 123])
def test_collision_before_hazard_preserves_evidence_and_continues(
    tmp_path, identifier
):
    scene, world, _, events, records, _ = scene_fixture(tmp_path)
    tick(scene, world, 1)
    event = collision(world, identifier)
    frame = world.frame
    scene.backend._collision(event)
    # Callback copies primitives, rather than keeping the native event alive.
    event.other_actor.type_id = "changed"
    event.normal_impulse.x = 999
    tick(scene, world, 0.05)
    assert scene.state == "SCHEDULED"
    scene.finalize("USER_EXIT")
    failure = next(p for n, p in events if n == "m2_hazard_driving_event")
    evidence = failure["collisions"][0]
    assert evidence["actor_id"] == identifier
    assert evidence["other_actor_type_id"] == "static.test_barrier"
    assert evidence["other_actor_semantic_tags"] == [1, 2]
    assert evidence["normal_impulse"] == {"x": 3.0, "y": 4.0, "z": 0.0}
    assert evidence["ego_actor_id"] == world.vehicle.id
    assert evidence["sensor_pose"]["x"] == 23
    assert evidence["collision_frame_ego"]["frame"] == frame
    assert evidence["collision_frame_ego"]["pose"]["x"] == 12
    assert failure["observation_context"]["frame"] > frame
    assert failure["observation_context"]["road_id"] == 47
    assert failure["hazard_actors_spawned"] is False
    assert scene.backend.lead is None and scene.backend.cargo is None
    assert records[0]["partial_data"]
    assert records[0]["driving_event_counts"]["COLLISION"] == 1
    assert failure["session_action"] == "CONTINUE"


def test_missing_optional_metadata_and_snapshot_do_not_discard_collision(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    scene.backend._collision(
        SimpleNamespace(other_actor=SimpleNamespace(id=0), frame=-5, timestamp=0)
    )
    tick(scene, world, 0.1)
    scene.finalize("USER_EXIT")
    failure = next(p for n, p in events if n == "m2_hazard_driving_event")
    evidence = failure["collisions"][0]
    assert evidence["actor_id"] == 0
    assert evidence["collision_frame_ego"] is None
    assert evidence["other_actor_type_id"] is None
    assert evidence["normal_impulse"] is None
