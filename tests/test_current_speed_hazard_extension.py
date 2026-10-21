"""Current-speed exposure and continued input after the nominal M2 boundary."""

from collections import defaultdict
from types import SimpleNamespace

import pytest

from src.experiment.automation_interaction import DriverInput
from src.p7.types import P7Error
from tests.m2_hazard_fakes import Waypoint
from tests.test_clear_road_hazard import setup
from tests.test_m2_hazard_v1 import tick


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize("lane_y", [-3.5, 0, 3.5, 7])
@pytest.mark.parametrize("speed_kmh", [10, 30, 60, 90, 120])
def test_current_speed_four_second_clearance_on_every_lane_without_target_rpc(
    tmp_path, monkeypatch, mode, lane_y, speed_kmh
):
    scene, world, _, events, records, _ = setup(tmp_path, mode)
    def forbidden():
        raise AssertionError("Road speed limit is not an exposure condition")
    monkeypatch.setattr(world.vehicle, "get_speed_limit", forbidden)
    tick(scene, world, scene.planned - 6, speed=speed_kmh / 3.6, y=lane_y)
    for _ in range(90):
        tick(scene, world, .1, speed=speed_kmh / 3.6, y=lane_y)
        if scene.first_display:
            break
    assert scene.scene_failure is None, scene.last_reason
    assert scene.first_display and scene.first_display["normal_window_candidate"]
    placement = next(p for n, p in events if n == "m2_hazard_obstacle_reveal_requested")
    assert placement["placement_speed_kmh"] == pytest.approx(speed_kmh)
    assert placement["requested_bumper_clearance_m"] == pytest.approx(speed_kmh / 3.6 * 4)
    assert scene.first_geometry["cargo_pose"]["y"] == pytest.approx(lane_y)
    assert scene.first_geometry["ttc_candidate_s"] == pytest.approx(3.9, abs=.02)
    assert world.vehicle.applied_controls == world.vehicle.transform_commands == []
    assert scene.session.bundle.automation_runtime.state.control_mode.value == mode
    assert not scene.should_extend_drive(780, 780)
    scene.finalize("M2_END")
    assert all(placement[k] == v for k, v in records[-1]["placement"].items())


@pytest.mark.parametrize("block", ["stopped", "very_slow", "junction"])
@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
def test_wait_past_780_then_expose_and_record_response_window(tmp_path, monkeypatch, block, mode):
    scene, world, clock, events, records, _ = setup(tmp_path, mode)
    monkeypatch.setattr(Waypoint, "is_junction", block == "junction")
    speed = 0 if block == "stopped" else 9.9 / 3.6 if block == "very_slow" else 20 / 3.6
    tick(scene, world, scene.planned - 6, speed=speed)
    assert not scene.should_extend_drive(scene.elapsed, 780)
    assert scene.duration_extension is None
    for boundary in (601, 780, 820):
        tick(scene, world, boundary - clock(), speed=speed)
        assert scene.first_display is None and not scene.claimed
        if boundary >= 780:
            assert scene.should_extend_drive(clock(), 780)
    assert scene.scene_failure is None
    assert scene.last_reason == ("JUNCTION_IN_HAZARD_CORRIDOR" if block == "junction" else "STOPPED_OR_LOW_SPEED")
    monkeypatch.setattr(Waypoint, "is_junction", False)
    for _ in range(70):
        tick(scene, world, .1, speed=20 / 3.6)
        if scene.first_display:
            break
    assert scene.first_display and scene.first_display["normal_window_candidate"]
    onset = scene.first_display["frame_callback_active_elapsed_s"]
    assert onset > 820
    assert scene.should_extend_drive(onset + 9.9, 780)
    # Keep all ordinary samples while the participant avoids the obstacle.
    for _ in range(102):
        tick(scene, world, .1, speed=20 / 3.6, y=3.5)
    assert not scene.should_extend_drive(clock(), 780)
    assert scene.duration_extension["status"] == "COMPLETE"
    assert sum(n == "m2_hazard_obstacle_reveal_requested" for n, _ in events) == 1
    assert world.vehicle.applied_controls == []
    scene.finalize("M2_END")
    assert records[-1]["duration_extension"]["extension_s"] > 50
    assert records[-1]["occurrence_result"] == "EXPOSURE_CANDIDATE"


def test_acceleration_does_not_require_matching_a_constant_speed(tmp_path):
    scene, world, _, _, _, _ = setup(tmp_path)
    tick(scene, world, scene.planned - 6, speed=4)
    for i in range(90):
        tick(scene, world, .1, speed=4 + i * .15)
        if scene.first_display:
            break
    assert scene.first_display and scene.scene_failure is None
    assert scene.backend.reveal_record["placement_speed_kmh"] < 90
    scene.finalize("M2_END")


def test_last_moment_readiness_failure_retries_without_consuming_occurrence(tmp_path, monkeypatch):
    scene, world, _, _, _, _ = setup(tmp_path)
    reveal = scene.backend.reveal
    attempts = []
    def changed_corridor(sample, **kwargs):
        attempts.append(scene.elapsed)
        if len(attempts) == 1:
            assert not scene.claimed
            raise P7Error("JUNCTION_IN_HAZARD_CORRIDOR")
        return reveal(sample, **kwargs)
    monkeypatch.setattr(scene.backend, "reveal", changed_corridor)
    tick(scene, world, scene.planned - 6)
    for _ in range(130):
        tick(scene, world, .1)
        if scene.first_display:
            break
    assert len(attempts) == 2 and scene.first_display and scene.scene_failure is None
    assert len(list((tmp_path / "occ").glob("*.json"))) == 1
    scene.finalize("M2_END")


def test_unsuitable_camera_geometry_waits_before_claim_then_exposes(tmp_path, monkeypatch):
    scene, world, _, events, _, _ = setup(tmp_path)
    camera = scene.session.viewer.feeds[0].sensor
    original = camera.get_transform
    def backward():
        pose = original()
        pose.rotation.yaw += 180
        return pose
    monkeypatch.setattr(camera, "get_transform", backward)
    tick(scene, world, 800)
    for _ in range(60):
        tick(scene, world, .1)
    assert scene.scene_failure is None and not scene.claimed
    assert scene.backend.motion_started is None
    assert any(n == "m2_hazard_readiness_evaluated"
               and p["checks"].get("placement_geometry", {}).get("status") == "FAIL"
               for n, p in events)
    assert scene.should_extend_drive(scene.elapsed, 780)
    monkeypatch.setattr(camera, "get_transform", original)
    for _ in range(60):
        tick(scene, world, .1)
        if scene.first_display:
            break
    assert scene.first_display and scene.scene_failure is None
    assert scene.last_readiness["checks"]["placement_geometry"]["status"] == "PASS"
    scene.finalize("USER_EXIT")


def test_logging_outage_keeps_exposure_and_bounded_input_buffer_then_recovers(tmp_path, monkeypatch):
    scene, world, clock, _, records, _ = setup(tmp_path)
    original = scene.services.emit
    def disk_full(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(scene.services, "emit", disk_full)
    tick(scene, world, scene.planned - 6)
    for _ in range(500):
        tick(scene, world, .1, y=3.5 if scene.first_display else 0)
    assert scene.first_display and scene.scene_failure is None
    assert len(scene.raw) <= 120 and len(scene._raw_write_errors) <= 32
    assert scene._dropped_raw_samples > 300
    assert scene.continuation_errors
    monkeypatch.setattr(scene.services, "emit", original)
    tick(scene, world, 1.1, y=3.5)
    scene.finalize("USER_EXIT")
    assert records[-1]["raw_storage"]["pending_sample_count"] == 0
    assert records[-1]["raw_storage"]["dropped_samples_during_storage_failure"] > 300
    assert world.vehicle.applied_controls == []


@pytest.mark.parametrize("exit_kind", ["normal", "escape", "collision", "scene_error"])
def test_real_driver_loop_keeps_pedals_steering_and_exit_during_extension(monkeypatch, exit_kind):
    from src.scenario import driver_view
    from tests.test_driver_view import FakeHero, FakeWorld
    from tests.test_driver_view_runtime import FakeFrameClock
    from src.vehicle.driving_mode import DrivingMode

    now = [0.0]
    times = iter((0, 780, 781, 792))
    def clock():
        now[0] = next(times)
        return now[0]
    monkeypatch.setattr(driver_view.time, "monotonic", clock)
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", FakeFrameClock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    monkeypatch.setattr(driver_view.pygame.key, "get_pressed", lambda: defaultdict(bool))
    def events():
        if exit_kind in ("escape", "scene_error") and now[0] >= 781:
            return [driver_view.pygame.event.Event(driver_view.pygame.KEYDOWN, key=driver_view.pygame.K_ESCAPE)]
        return []
    monkeypatch.setattr(driver_view.pygame.event, "get", events)
    samples, collision_waits, displays = [], [], []
    class Scene:
        started_at = 0.0
        collision_stop = None
        duration_extension = {"status": "ACTIVE"}
        # Exercise the same duration decision used by the physical scene.
        from src.p8.clear_road_hazard import ClearRoadM2Hazard as _type
        should_extend_drive = _type.should_extend_drive
        closed = False
        first_display = None
        scene_failure = RuntimeError("scene failed") if exit_kind == "scene_error" else None
        config = SimpleNamespace(post_exposure_drive_s=10)
        def emit(self, *args):
            pass
        def after_control(self, driver, *_):
            samples.append((now[0], driver))
            if now[0] >= 781:
                self.first_display = {"frame_callback_active_elapsed_s": 781}
                if exit_kind == "collision":
                    self.collision_stop = SimpleNamespace(wait=lambda: collision_waits.append("confirmed"))
        def after_display_completed(self, frame):
            displays.append(now[0])
    hero = FakeHero()
    viewer = driver_view.DriverView(FakeWorld(), hero,
                                   driver_view.DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL))
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(viewer, "_draw", lambda screen: None)
    driver = DriverInput(throttle=.3, brake=.2, steering=.1)
    monkeypatch.setattr(viewer, "_collect_driver_input", lambda events: driver)
    exited = viewer.run(780, active_scene=Scene())
    assert exited is (exit_kind in ("escape", "scene_error"))
    assert samples[1] == (780, driver)
    assert len(hero.control_calls) == len(samples)
    assert all(c.throttle == pytest.approx(.3) and c.brake == pytest.approx(.2)
               and c.steer == pytest.approx(.1) for c in hero.control_calls)
    assert 780 in displays
    assert collision_waits == (["confirmed"] if exit_kind == "collision" else [])
    if exit_kind == "normal":
        assert samples[-1][0] == 781
        assert viewer.drive_extension["status"] == "COMPLETE"
