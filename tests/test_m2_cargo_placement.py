"""Delayed setter/readback regression; requires the project's CARLA Python API."""

import carla
import pytest

from src.p7.types import P7Error
from tests.m2_hazard_fakes import Actor
from tests.test_m2_hazard_v1 import scene_fixture, tick


def copy_pose(pose):
    return carla.Transform(carla.Location(pose.location), pose.rotation)


class DelayedCargo(Actor):
    """Unlike the old fake, spawn/setter inputs do not alias the readback."""

    def __init__(self, identifier, blueprint, pose):
        super().__init__(identifier, blueprint, copy_pose(pose))
        self.pending = None
        # Measured box01 bounds from the 9813 pilot; not production constants.
        self.bounding_box = carla.BoundingBox(
            carla.Location(z=0.34022006392478943),
            carla.Vector3D(
                0.32444822788238525, 0.32773923873901367, 0.3457734286785126
            ),
        )

    def set_transform(self, pose):
        self.transform_commands.append(copy_pose(pose))
        self.pending = copy_pose(pose)

    def apply_pending(self):
        self.transform, self.pending = self.pending, None


def delayed_scene(tmp_path, monkeypatch, mode="MANUAL"):
    fixture = scene_fixture(tmp_path, mode=mode)
    scene, world, _, _, _, _ = fixture
    spawn = world.try_spawn_actor

    def spawn_with_delayed_cargo(blueprint, pose):
        if blueprint.id != "static.prop.box01":
            return spawn(blueprint, pose)
        cargo = DelayedCargo(100 + len(world.actors), blueprint.id, pose)
        world.actors.append(cargo)
        return cargo

    monkeypatch.setattr(world, "try_spawn_actor", spawn_with_delayed_cargo)
    return fixture


def payloads(events, suffix):
    return [payload for name, payload in events if name == "m2_hazard_" + suffix]


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
def test_delayed_height_readback_is_pending_then_confirmed_not_cargo_moved(
    tmp_path, monkeypatch, mode
):
    scene, world, _, events, _, _ = delayed_scene(tmp_path, monkeypatch, mode)
    try:
        tick(scene, world, scene.next_attempt)
        cargo = scene.backend.cargo
        assert cargo.get_transform().location.z == 0
        assert scene.backend.cargo_pose.location.z == pytest.approx(
            0.025553365, abs=1e-7
        )
        assert scene.backend.cargo_placement_frame is None
        assert len(payloads(events, "cargo_placement_requested")) == 1
        assert not payloads(events, "cargo_placed")
        tick(scene, world, 0.05)
        old_snapshot = world.get_snapshot()
        assert scene.state == "OCCLUDED"
        assert scene.backend.cargo_placement_frame is None
        assert scene.last_geometry is None
        cargo.apply_pending()
        tick(scene, world, 0.05)
        assert scene.last_geometry["cargo_stationary"] is True
        placed = payloads(events, "cargo_placed")
        assert len(placed) == 1
        assert placed[0]["frame"] > placed[0]["request_after_frame"]
        assert placed[0]["pose"]["z"] == pytest.approx(0.025553365, abs=1e-7)
        assert scene.backend.geometry(old_snapshot) is None
        for _ in range(150):
            tick(scene, world, 0.05)
            if scene.first_geometry is not None:
                break
        assert len(payloads(events, "cargo_placed")) == 1
        assert len(payloads(events, "lead_move_command")) == 1
        assert scene.first_geometry["normal_window_candidate"] is True
        assert scene.first_display is not None
        assert len(cargo.transform_commands) == 1
        assert not world.vehicle.transform_commands
        assert not world.vehicle.applied_controls
    finally:
        scene.finalize("USER_EXIT")


def test_unacknowledged_placement_stops_before_reveal_and_keeps_partial_raw(
    tmp_path, monkeypatch
):
    scene, world, clock, events, records, _ = delayed_scene(tmp_path, monkeypatch)
    tick(scene, world, scene.next_attempt)
    with pytest.raises(P7Error, match="CARGO_PLACEMENT_UNCONFIRMED") as caught:
        tick(scene, world, scene.command_at - clock() + 0.01)
    scene.finalize("ERROR", caught.value)
    assert not payloads(events, "lead_move_command")
    assert not payloads(events, "cargo_placed")
    assert records[0]["partial_data"]
    assert records[0]["first_geometry"] is None
    assert records[0]["raw_storage"]["pending_sample_count"] == 0
    assert records[0]["cleanup"]["remaining_actor_ids"] == []
    failure = payloads(events, "unexpected_failure")[-1]
    assert failure["reason"] == "CARGO_PLACEMENT_UNCONFIRMED"
    assert failure["placement_observation"]["position_error_m"] == pytest.approx(
        0.025553365, abs=1e-7
    )


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_real_displacement_after_confirmation_still_aborts(tmp_path, monkeypatch, axis):
    scene, world, _, events, records, _ = delayed_scene(tmp_path, monkeypatch)
    tick(scene, world, scene.next_attempt)
    cargo = scene.backend.cargo
    cargo.apply_pending()
    tick(scene, world, 0.05)
    moved = copy_pose(cargo.get_transform())
    setattr(moved.location, axis, getattr(moved.location, axis) + 0.02)
    cargo.transform = moved
    with pytest.raises(P7Error, match="M2_V1_CARGO_MOVED") as caught:
        tick(scene, world, 0.05)
    scene.finalize("ERROR", caught.value)
    assert len(payloads(events, "cargo_placed")) == 1
    assert records[0]["scene_reason"] == "CARGO_MOVED"


def test_rendering_cannot_acknowledge_or_rebaseline_placement(tmp_path, monkeypatch):
    scene, world, _, events, _, _ = delayed_scene(tmp_path, monkeypatch)
    try:
        tick(scene, world, scene.next_attempt)
        scene.backend.cargo.apply_pending()
        snapshot = world.step(0.05)
        camera = scene.session.viewer.feeds[0].sensor.get_transform()
        assert scene.backend.geometry(snapshot, camera) is None
        assert scene.backend.cargo_placement_frame is None
        assert not payloads(events, "cargo_placed")
        geometry = scene.backend.geometry(snapshot)
        assert geometry["cargo_stationary"]
        assert len(payloads(events, "cargo_placed")) == 1
    finally:
        scene.finalize("USER_EXIT")


def test_same_frame_is_not_a_placement_acknowledgement(tmp_path, monkeypatch):
    scene, world, _, events, _, _ = delayed_scene(tmp_path, monkeypatch)
    try:
        tick(scene, world, scene.next_attempt)
        scene.backend.cargo.apply_pending()
        assert scene.backend.geometry(world.get_snapshot()) is None
        assert not payloads(events, "cargo_placed")
        tick(scene, world, 0.05)
        assert scene.backend.cargo_placement_frame == world.frame
    finally:
        scene.finalize("USER_EXIT")
