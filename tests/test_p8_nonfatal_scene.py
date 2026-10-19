"""Fault injection through the actual nonfatal operator scene boundary."""

import carla
import pytest

from src.p7.types import P7Error
from src.p8.operator_m2_continuation import ContinuingM2Hazard
from tests.test_m2_hazard_v1 import prepare, scene_fixture, tick


def scene_at(tmp_path, mode="MANUAL"):
    return scene_fixture(tmp_path, mode=mode, scene_class=ContinuingM2Hazard)


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize(
    "failure",
    [
        "CARGO_MOVED",
        "CARGO_PLACEMENT_UNCONFIRMED",
        "INVALID_EXPOSURE",
        "PREPARED_WINDOW_EXPIRED",
        "HAZARD_ACTOR_LOST",
        "SPAWN_FAILED",
    ],
)
def test_scene_failures_do_not_end_drive_or_duplicate_opportunity(
    tmp_path, monkeypatch, mode, failure
):
    scene, world, clock, events, records, _ = scene_at(tmp_path, mode)
    prepare(scene, world)

    def fail(_):
        raise P7Error(failure)

    monkeypatch.setattr(scene.backend, "geometry", fail)
    tick(scene, world, 0.1)
    assert scene.state == "FAILED" and not scene.closed
    assert world.vehicle.destroy_count == 0
    actors = len(world.actors)
    tick(scene, world, 700 - clock())
    tick(scene, world, 780 - clock())
    scene.finalize("M2_END")
    assert len(world.actors) == actors
    assert records[-1]["execution_status"] == "FAILED"
    assert records[-1]["occurrence_result"] == "INVALID"
    assert records[-1]["m2_active_elapsed_s"] == pytest.approx(780)
    assert records[-1]["raw_storage"]["pending_sample_count"] == 0
    raw = [s for n, p in events if n == "m2_hazard_raw_samples" for s in p["samples"]]
    assert raw[-1]["m2_active_elapsed_s"] == pytest.approx(780)
    assert sum(n == "m2_hazard_scene_failed_continue" for n, _ in events) == 1
    end = next(p for n, p in events if n == "m2_hazard_end")
    assert end["occurrence_result"] == "INVALID"
    assert end["execution_status"] == "FAILED"


def test_real_cargo_movement_invalidates_and_cleans_only_scene(tmp_path):
    scene, world, _, _, records, _ = scene_at(tmp_path)
    prepare(scene, world)
    tick(scene, world, 0.1)
    cargo = scene.backend.cargo
    pose = cargo.get_transform()
    pose.location.x += 0.1
    cargo.set_transform(pose)
    tick(scene, world, 0.1)
    assert scene.scene_failure is not None
    assert "CARGO_MOVED" in str(scene.scene_failure)
    assert cargo.destroy_count == 1
    assert world.vehicle.destroy_count == 0
    scene.finalize("M2_END")
    assert records[-1]["occurrence_result"] == "INVALID"


def test_actual_early_visibility_keeps_evidence_without_aborting(tmp_path):
    scene, world, _, _, records, _ = scene_at(tmp_path)
    scene.planned, scene.next_attempt = 400, 394
    prepare(scene, world)
    lead = scene.backend.lead
    lead.transform = carla.Transform(carla.Location(x=lead.transform.location.x, y=3.5))
    tick(scene, world, 0.1)
    assert "INVALID_EXPOSURE" in str(scene.scene_failure)
    scene.finalize("M2_END")
    assert records[-1]["first_geometry"]["normal_window_candidate"] is False


def test_display_failure_and_cleanup_failure_do_not_close_drive(tmp_path, monkeypatch):
    scene, world, _, _, records, _ = scene_at(tmp_path)
    prepare(scene, world)
    scene.backend.lead.destroy = lambda: False

    def fail(_):
        raise P7Error("INVALID_DISPLAY_EXPOSURE")

    monkeypatch.setattr(scene.backend, "display_geometry", fail)
    tick(scene, world, 0.1)
    assert not scene.closed
    scene.finalize("M2_END")
    assert records[-1]["cleanup"]["remaining_actor_ids"]
    assert records[-1]["occurrence_result"] == "INVALID"


def test_setup_failure_does_not_prevent_m2_input_recording(tmp_path, monkeypatch):
    from src.p7.moving_backend import MovingHazardBackend

    monkeypatch.setattr(
        MovingHazardBackend,
        "start_observation",
        lambda _: (_ for _ in ()).throw(RuntimeError("sensor setup")),
    )
    scene, world, _, _, records, _ = scene_at(tmp_path)
    tick(scene, world, 780)
    scene.finalize("M2_END")
    assert records[-1]["occurrence_result"] == "INVALID"
    assert records[-1]["m2_active_elapsed_s"] == 780


def test_storage_failure_is_bounded_and_not_reported_as_success(tmp_path, monkeypatch):
    scene, world, _, _, records, _ = scene_at(tmp_path)

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(scene.services, "emit", fail)
    for _ in range(150):
        tick(scene, world, 1)
    scene.finalize("M2_END")
    assert records[-1]["recording_errors"]
    assert records[-1]["partial_data"]
    assert records[-1]["raw_storage"]["pending_sample_count"] > 0
    assert len(scene.raw) <= 120


def test_keyboard_interrupt_remains_explicit_stop(tmp_path, monkeypatch):
    scene, world, _, _, _, _ = scene_at(tmp_path)
    monkeypatch.setattr(
        scene.backend, "failures", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        tick(scene, world, 1)
