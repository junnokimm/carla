import io
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pygame
import pytest

from src.p6.aoi import Rect
from src.p6.models import DiscoveredDevice
from src.p6.pilot_aoi import classify_screen, layout_for_view
from src.p6.tobii.client import TobiiProcessSource
from src.p6.tobii.protocol import TobiiSampleDecoder
from src.p6.validation.mapping import WindowGeometry
from src.p8.model import Stage
from src.p8.pilot_gaze import PilotGaze, collect_pilot_gaze
from src.scenario.driver_view import DriverViewConfig, calculate_layout
from src.scenario.driver_view_presentation import DriverViewPresentation
from tests.test_p6_tobii_protocol import gaze_record


def geometry(size=(1280, 720), client=None, monitor=None):
    bounds = Rect(0, 0, *size)
    return WindowGeometry(bounds, monitor or bounds, client or bounds, size, 1, "DISPLAY1", 96)


@pytest.mark.parametrize("rear", [False, True])
def test_live_aois_follow_raised_mirrors_without_overlap(rear):
    config = replace(DriverViewConfig(), rear_mirror_enabled=rear)
    layout = layout_for_view(config)
    assert not layout.validate().issues
    actual = calculate_layout(config)
    for name, rect in (("LEFT_MIRROR", actual.left_mirror), ("RIGHT_MIRROR", actual.right_mirror)):
        x, y = rect.x + rect.width / 2, rect.y + rect.height / 2
        assert classify_screen(x / 1280, y / 720, geometry(), layout)[0] == name
        assert not layout.forward.contains(SimpleNamespace(x=x, y=y))
    assert classify_screen(.5, .4, geometry(), layout)[0] == "FORWARD"
    assert classify_screen(.5, .9, geometry(), layout)[0] == "NO_AOI"
    assert classify_screen(.875, .2, geometry(), layout)[0] == ("REAR_MIRROR" if rear else "FORWARD")


@pytest.mark.parametrize("size", [(1920, 1080), (1920, 1200), (1024, 768)])
def test_fullscreen_letterbox_matches_rendered_target(size):
    from src.scenario.driver_view_presentation import aspect_fit_rect
    layout = layout_for_view(DriverViewConfig())
    view = aspect_fit_rect((1280, 720), size)
    for aoi, point in (("FORWARD", (640, 280)), ("LEFT_MIRROR", (144, 520)),
                       ("RIGHT_MIRROR", (1136, 520))):
        x = (view.x + point[0] * view.width / 1280) / size[0]
        y = (view.y + point[1] * view.height / 720) / size[1]
        assert classify_screen(x, y, geometry(size), layout)[0] == aoi
    if view.y:
        assert classify_screen(.5, 0, geometry(size), layout)[0] == "NO_AOI"


def test_offset_monitor_and_dpi_mapping():
    layout = layout_for_view(DriverViewConfig())
    observed = geometry(client=Rect(2100, 100, 1920, 1080), monitor=Rect(1920, 0, 2560, 1440))
    x = (2100 - 1920 + 144 * 1.5) / 2560
    y = (100 + 520 * 1.5) / 1440
    assert classify_screen(x, y, observed, layout)[0] == "LEFT_MIRROR"
    assert classify_screen(-.1, .5, observed, layout)[0] == "NO_AOI"


def collector(tmp_path, **kwargs):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"tobii_python": "unused", "display_confirmed": True}))
    services = SimpleNamespace(directory=tmp_path / "run", context=SimpleNamespace(
        study_run_id="RUN", participant_id="P1"), clock=SimpleNamespace(
        origin=SimpleNamespace(monotonic_ns=time.monotonic_ns())))
    return PilotGaze(services, Stage.TRAINING, DriverViewConfig(), config, **kwargs)


class Probe:
    def snapshot(self, hwnd, size):
        return geometry(size)

    def focused(self, hwnd):
        return True

    def close(self):
        pass


def sample():
    raw = gaze_record()
    raw["host_monotonic_ns"] = time.monotonic_ns()
    return TobiiSampleDecoder().decode(json.dumps(raw)).sample


class Source:
    def __init__(self, python, *, seconds, pilot):
        assert pilot and seconds > 780
        self.finished = threading.Event()
        self.running = False
        self.last_error = "RAW_CAPTURED"

    def discover(self):
        return (DiscoveredDevice("FAKE-001", "Tobii Pro Nano", True),)

    def select(self, serial):
        assert serial == "FAKE-001"

    def subscribe(self, callback):
        self.record_sink(json.dumps(gaze_record()))
        callback(sample())

    def unsubscribe(self):
        pass


def test_capture_saves_real_flag_stage_timing_and_summary(tmp_path):
    item = collector(tmp_path, source_factory=Source, probe_factory=Probe)
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item.start()
    deadline = time.monotonic() + 3
    while not item.counts["samples"] and time.monotonic() < deadline:
        time.sleep(.01)
    item.close()
    target = next((item.directory / "gaze").iterdir())
    rows = [json.loads(line) for line in (target / "gaze.jsonl").read_text().splitlines()]
    row = next(row for row in rows if row["record_type"] == "gaze")
    assert row["aoi"] == "FORWARD"
    assert row["synthetic"] is False
    assert row["stage"] == "TRAINING"
    assert row["clock_sync_validated"] is False
    assert json.loads((target / "summary.json").read_text())["counts"]["samples"] == 1


@pytest.mark.parametrize("failure", ["SDK_MISSING", "DISCONNECTED", "NO_DEVICE"])
def test_discovery_failure_is_saved_without_propagation(tmp_path, failure):
    class Failing(Source):
        def discover(self):
            raise RuntimeError(failure)
    item = collector(tmp_path, source_factory=Failing)
    item.start()
    item.thread.join(3)
    item.close()
    report = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    assert report["status"] == "FAILED"
    assert report["session_action"] == "CONTINUE"
    assert failure in report["errors"][0]


def test_storage_error_and_context_failure_do_not_change_driving_result(tmp_path, monkeypatch):
    from src.p8 import pilot_gaze
    item = collector(tmp_path)
    monkeypatch.setattr(pilot_gaze, "PilotGaze", lambda *args: item)
    monkeypatch.setattr(item, "start", lambda: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(item, "close", lambda: (_ for _ in ()).throw(OSError("disk full")))
    viewer = SimpleNamespace(_config=DriverViewConfig())
    with collect_pilot_gaze(viewer, None, Stage.TRAINING, Path("fake")):
        driving_result = "completed"
    assert driving_result == "completed"
    assert viewer.gaze_observer is None


def test_mapping_failure_preserves_raw_sample_and_never_labels_tablet(tmp_path):
    class BrokenProbe(Probe):
        def snapshot(self, hwnd, size):
            raise OSError("window gone")
    item = collector(tmp_path, probe_factory=BrokenProbe)
    item.config = {"display_confirmed": True}
    item.wire = item.gaze = io.StringIO()
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item._sample(sample())
    row = json.loads(item.gaze.getvalue())
    assert row["aoi"] == "NO_AOI"
    assert row["aoi_reason"] == "MAPPING_UNAVAILABLE"
    assert row["sample"]["source_metadata"]["raw_sdk_json"]


def test_renderer_observer_failure_cannot_interrupt_presentation():
    screen = pygame.Surface((1280, 720))
    view = DriverViewPresentation((1280, 720), screen)
    view.canvas.fill((12, 34, 56))
    view.gaze_observer = lambda _: (_ for _ in ()).throw(RuntimeError("observer failed"))
    view.present()
    assert screen.get_at((640, 360))[:3] == (12, 34, 56)
    assert view.gaze_observer is None


def test_only_explicit_pilot_source_enables_long_capture():
    with pytest.raises(RuntimeError):
        TobiiProcessSource(Path("unused"), seconds=7200)
    source = TobiiProcessSource(Path("unused"), seconds=7200, pilot=True)
    source._serial = "FAKE-001"
    assert "pilot" in source._command("gaze")
    assert "7200" in source._command("gaze")


@pytest.mark.parametrize("terminal", ["DISCONNECTED", "QUEUE_OVERFLOW", "WORKER_EXIT"])
def test_stream_failure_is_nonfatal_and_partial_samples_remain(tmp_path, terminal):
    class TerminalSource(Source):
        def subscribe(self, callback):
            super().subscribe(callback)
            self.last_error = terminal
            self.finished.set()
    item = collector(tmp_path, source_factory=TerminalSource)
    item.start()
    item.thread.join(3)
    item.close()
    report = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    assert report["counts"]["samples"] == 1
    assert report["status"] == "FAILED"
    assert any(terminal in error for error in report["errors"])


def test_disk_write_failure_is_contained_and_reported(tmp_path, monkeypatch):
    item = collector(tmp_path, source_factory=Source)
    monkeypatch.setattr(item, "_raw", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    item.start()
    item.thread.join(3)
    item.close()
    report = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    assert report["status"] == "FAILED"
    assert any("disk full" in error for error in report["errors"])


def test_driving_exception_is_not_swallowed_by_optional_capture():
    with pytest.raises(RuntimeError, match="driving error"), collect_pilot_gaze(
        None, None, Stage.TRAINING, None
    ):
        raise RuntimeError("driving error")


def test_frozen_hazard_exception_identity_is_preserved():
    from src.p7.types import P7Error
    original = P7Error("HAZARD_GEOMETRY_SAMPLE_MISMATCH")
    with pytest.raises(P7Error) as observed, collect_pilot_gaze(None, None, Stage.TRAINING, None):
        raise original
    assert observed.value is original


def test_completed_sidecar_is_exported_and_pending_files_are_excluded(tmp_path):
    from zipfile import ZipFile

    from src.p8.operator_results import export_operator_results

    item = collector(tmp_path, source_factory=Source)
    item.start()
    deadline = time.monotonic() + 3
    while not item.counts["samples"] and time.monotonic() < deadline:
        time.sleep(.01)
    item.close()
    (item.directory / "session_manifest.json").write_text(json.dumps(
        {"state": "SESSION_COMPLETE", "run_id": item.directory.name}))
    (item.directory / ".gaze_pending" / "incomplete.jsonl").write_text("partial")
    result = export_operator_results(item.directory, closed=True)
    assert result.error is None
    with ZipFile(result.archive) as archive:
        assert any(name.endswith("/raw_gaze.jsonl") for name in archive.namelist())
        assert any(name.endswith("/summary.json") for name in archive.namelist())
        assert not any(".gaze_pending" in name for name in archive.namelist())


def test_missing_config_keeps_driving_and_produces_failure_evidence(tmp_path):
    item = collector(tmp_path)
    item.config_path = tmp_path / "absent.json"
    item.start()
    item.thread.join(3)
    item.close()
    report = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    assert report["status"] == "FAILED"


def test_monitor_change_does_not_get_relabelled_as_tracker_display(tmp_path):
    item = collector(tmp_path, probe_factory=Probe)
    item.config = {"display_confirmed": True, "monitor_name": "OTHER"}
    item.wire = item.gaze = io.StringIO()
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item._sample(sample())
    row = json.loads(item.gaze.getvalue().splitlines()[-1])
    assert row["aoi"] == "NO_AOI"
    assert row["aoi_reason"] == "TRACKER_MONITOR_MISMATCH"


def test_validation_skips_disabled_rear_mirror(tmp_path):
    from src.p6.validation.run import ValidationRun
    run = ValidationRun(tmp_path / "validation", layout_for_view(DriverViewConfig()))
    try:
        assert run.sequence.targets == ("FORWARD", "LEFT_MIRROR", "RIGHT_MIRROR", "MISSING")
    finally:
        run.sequence.abort(time.monotonic_ns(), "TEST")
        run.finish()
