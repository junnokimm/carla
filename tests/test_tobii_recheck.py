from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.tobii import display_area_smoke, recheck


def report():
    return {"monitor_runs": [], "tablet_runs": [], "errors": []}


class PreviewWorld:
    def __init__(self, *, synchronous=False, occupied=1):
        self.synchronous = synchronous
        self.occupied = occupied
        self.attempts = []
        self.destroyed = 0
        self.frozen = []
        self.points = [SimpleNamespace(index=i, location=SimpleNamespace(
            distance=lambda other, i=i: abs(i - other.index), index=i)) for i in range(350)]
        self.actor = SimpleNamespace(id=77, is_alive=True, destroy=self.destroy,
                                     set_simulate_physics=self.frozen.append)

    def get_map(self):
        return SimpleNamespace(name="Carla/Maps/Town04", get_spawn_points=lambda: self.points)

    def get_settings(self):
        return SimpleNamespace(synchronous_mode=self.synchronous)

    def get_blueprint_library(self):
        return SimpleNamespace(find=lambda name: SimpleNamespace(
            has_attribute=lambda name: True, set_attribute=lambda *args: None))

    def try_spawn_actor(self, blueprint, point):
        self.attempts.append(point.index)
        return None if len(self.attempts) <= self.occupied else self.actor

    def destroy(self):
        self.destroyed += 1
        return True


def mock_monitor(monkeypatch, world):
    client = SimpleNamespace(set_timeout=lambda value: None, get_world=lambda: world)
    monkeypatch.setitem(sys.modules, "carla", SimpleNamespace(Client=lambda *args: client))
    monkeypatch.setattr(recheck, "confirm", lambda message: None)
    monkeypatch.setattr(recheck, "prompt", lambda message: "")


def test_occupied_344_uses_nearby_point_and_cleans_only_owned_actor(monkeypatch, tmp_path):
    world = PreviewWorld()
    mock_monitor(monkeypatch, world)
    commands = []
    monkeypatch.setattr(recheck, "child", lambda command, *args: commands.append(command) or 0)
    saved = report()
    recheck.monitor_checks(tmp_path, tmp_path / "tobii.exe", "FAKE", 3, saved)
    assert world.attempts == [344, 343]
    assert world.frozen == [False] and world.destroyed == 1
    assert len(saved["monitor_runs"]) == 3
    assert [c[c.index("--run-id") + 1] for c in commands] == ["monitor_01", "monitor_02", "monitor_03"]
    assert all("--windowed" not in c for c in commands)


@pytest.mark.parametrize("failure", [RuntimeError("capture failed"), subprocess.TimeoutExpired("capture", 225), KeyboardInterrupt()])
def test_capture_failure_always_cleans_preview(monkeypatch, tmp_path, failure):
    world = PreviewWorld(occupied=0)
    mock_monitor(monkeypatch, world)

    def fail(*args):
        raise failure

    monkeypatch.setattr(recheck, "child", fail)
    with pytest.raises(type(failure)):
        recheck.monitor_checks(tmp_path, tmp_path / "tobii.exe", "FAKE", 3, report())
    assert world.destroyed == 1


def test_sync_world_rejected_before_actor_creation():
    world = PreviewWorld(synchronous=True)
    with pytest.raises(RuntimeError, match="동기 모드"):
        recheck.spawn_preview(world)
    assert not world.attempts and world.destroyed == 0


def test_all_spawn_points_occupied_is_bounded_without_deleting_vehicles():
    world = PreviewWorld(occupied=100)
    with pytest.raises(RuntimeError, match="30개"):
        recheck.spawn_preview(world)
    assert len(world.attempts) == 30 and world.destroyed == 0


@pytest.mark.parametrize("distance", ["24", "90", "nan", "inf"])
def test_bad_distance_prevents_collection(monkeypatch, tmp_path, distance):
    monkeypatch.setattr(recheck, "prompt", lambda message: distance)
    with pytest.raises(RuntimeError, match="45~85"):
        recheck.setup_confirmation(tmp_path)
    assert not (tmp_path / "setup.json").exists()


def test_tablet_failure_preserves_progress_and_skips_later_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(recheck, "prompt", lambda message: "")
    calls = []

    def capture(command, log, timeout):
        calls.append(command)
        return 2 if len(calls) == 3 else 0

    monkeypatch.setattr(recheck, "child", capture)
    saved = report()
    with pytest.raises(RuntimeError, match="tablet_top_left"):
        recheck.tablet_checks(tmp_path, tmp_path / "tobii.exe", "FAKE", saved)
    assert len(calls) == len(saved["tablet_runs"]) == 3
    assert all(c[c.index("--seconds") + 1] == "20" for c in calls)


def test_old_display_geometry_is_saved_then_rejected(monkeypatch, tmp_path):
    def capture(command, log, timeout):
        log.write_text(json.dumps({"status": "DISPLAY_AREA_READ", "width_mm": 618, "height_mm": 348}))
        return 0

    monkeypatch.setattr(recheck, "child", capture)
    with pytest.raises(RuntimeError, match="65인치"):
        recheck.display_snapshot(tmp_path / "tobii.exe", None, tmp_path)
    assert json.loads((tmp_path / "display_area.json").read_text())["width_mm"] == 618


def test_main_preserves_partial_manifest_when_user_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(recheck, "ROOT", tmp_path)
    monkeypatch.setattr(recheck, "display_snapshot", lambda *args: {"serial_number": "FAKE"})

    def stop(directory):
        raise recheck.UserStopped("SETUP_NOT_CONFIRMED")

    monkeypatch.setattr(recheck, "setup_confirmation", stop)
    assert recheck.main([]) == 2
    summary = next((tmp_path / "data/gaze_recheck").glob("*/summary.json"))
    assert json.loads(summary.read_text())["status"] == "USER_STOPPED"


@pytest.mark.parametrize("seconds", [10, 20])
def test_complete_capture_is_never_accuracy_pass_and_reruns_have_separate_folders(monkeypatch, tmp_path, seconds):
    monkeypatch.setattr(recheck, "ROOT", tmp_path)
    monkeypatch.setattr(recheck, "display_snapshot", lambda *args: {"serial_number": "FAKE"})
    monkeypatch.setattr(recheck, "setup_confirmation", lambda *args: None)
    monkeypatch.setattr(recheck, "prompt", lambda *args: "")
    calls = []
    monkeypatch.setattr(recheck, "child", lambda command, log, timeout: calls.append((command, timeout)) or 0)
    for _ in range(2):
        args = ["--tablet-only"] + (["--tablet-seconds", str(seconds)] if seconds != 20 else [])
        assert recheck.main(args) == 0
    assert len(calls) == 14
    assert all(c[c.index("--seconds") + 1] == str(seconds) and timeout == seconds + 20
               for c, timeout in calls)
    summaries = list((tmp_path / "data/gaze_recheck").glob("*/summary.json"))
    assert len(summaries) == 2
    for path in summaries:
        saved = json.loads(path.read_text())
        assert saved["status"] == "CAPTURED_NOT_VALIDATED"
        assert len(saved["tablet_runs"]) == 7
        assert saved["aoi_validated"] is False and saved["tablet_aoi_implemented"] is False
        assert saved["tablet_seconds_per_target"] == seconds
        assert all(run["requested_seconds"] == seconds for run in saved["tablet_runs"])


def test_profile_snapshot_keeps_exact_pre_capture_bytes_and_failure_is_nonfatal(monkeypatch, tmp_path):
    import hashlib

    monkeypatch.setattr(recheck, "ROOT", tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "p8_gaze.json").write_text('{"tablet_profile": "tablet.json"}', encoding="utf-8")
    original = b'{"profile_id":"original-placement"}\n'
    source = config / "tablet.json"
    source.write_bytes(original)
    destination = tmp_path / "capture"
    destination.mkdir()
    saved = recheck.tablet_profile_snapshot(destination)
    source.write_bytes(b'{"profile_id":"changed-later"}')
    assert (destination / saved["file"]).read_bytes() == original
    assert saved["sha256"] == hashlib.sha256(original).hexdigest()
    assert saved["profile_id"] == "original-placement"
    (config / "p8_gaze.json").write_text('{"tablet_profile": "missing.json"}', encoding="utf-8")
    assert recheck.tablet_profile_snapshot(destination)["status"] == "UNAVAILABLE"


def test_sdk_display_reader_uses_only_read_apis(monkeypatch):
    area = SimpleNamespace(width=1439, height=809, top_left=(0, 809, 0), top_right=(1439, 809, 0),
                           bottom_left=(0, 0, 0), bottom_right=(1439, 0, 0))
    tracker = SimpleNamespace(model="Tobii Pro Nano", serial_number="FAKE", address="fake",
                              device_name="", firmware_version="fake", get_display_area=lambda: area)
    monkeypatch.setattr(display_area_smoke, "load_sdk", lambda: SimpleNamespace(find_all_eyetrackers=lambda: [tracker]))
    saved = display_area_smoke.read_display_area()
    assert (saved["width_mm"], saved["height_mm"]) == (1439, 809)
    assert saved["physical_geometry_verified"] is False


def test_sdk_reader_rejects_ambiguous_device_selection(monkeypatch):
    tracker = SimpleNamespace(model="Tobii Pro Nano", serial_number="FAKE", address="fake",
                              device_name="", firmware_version="fake")
    monkeypatch.setattr(display_area_smoke, "load_sdk", lambda: SimpleNamespace(find_all_eyetrackers=lambda: [tracker, tracker]))
    with pytest.raises(RuntimeError, match="MULTIPLE_NANOS"):
        display_area_smoke.read_display_area()
