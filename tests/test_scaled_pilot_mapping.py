import io
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from src.p6.aoi import Rect
from src.p6.pilot_aoi import classify_screen, layout_for_view
from src.p6.validation.mapping import WindowGeometry, WindowSpansMonitors
from src.p8.m2_gaze_check import M2GazeCheck, record_decision
from src.p8.model import Stage
from src.p8.operator_results import export_operator_results
from src.p8.pilot_display import mapping_issue, mapping_signature
from src.p8.pilot_gaze import PilotGaze
from src.scenario.driver_view import DriverViewConfig
from src.scenario.driver_view_presentation import aspect_fit_rect
from tests.test_external_tablet_gaze import decoded, display_record, profile, record
from tests.test_m2_gaze_precheck import services, successful_attempt
from tests.test_pilot_gaze import Probe, Source, collector

POINTS = {"FORWARD": (640, 280), "LEFT_MIRROR": (144, 520), "RIGHT_MIRROR": (1136, 520)}


def four_k(surface=(1920, 1080), origin=(1920, 0), dpi=96):
    monitor = Rect(*origin, 3840, 2160)
    return WindowGeometry(Rect(-3840, -2160, 11520, 6480), monitor, monitor,
                          surface, 1, "DISPLAY2", dpi)


class FourKProbe(Probe):
    def snapshot(self, hwnd, size):
        return replace(four_k(size), hwnd=hwnd)


def prepare(item, observed=None):
    observed = observed or four_k()
    item.config = {"display_confirmed": True}
    item.gaze = item.wire = io.StringIO()
    item.presentation = (time.monotonic_ns(), observed.hwnd, observed.surface_size)
    item.last_present_ns = time.monotonic_ns()


def gaze_rows(item):
    return [row for row in map(json.loads, item.gaze.getvalue().splitlines()) if row["record_type"] == "gaze"]


@pytest.mark.parametrize("surface", [(1280, 720), (1920, 1080), (3840, 2160), (1024, 768), (1921, 1201)])
@pytest.mark.parametrize("origin,dpi", [((1920, 0), 96), ((-3840, -100), 144), ((0, 1080), 192)])
def test_physical_4k_surface_and_logical_canvas_use_renderer_transform(surface, origin, dpi):
    observed = four_k(surface, origin, dpi)
    layout = layout_for_view(DriverViewConfig())
    view = aspect_fit_rect((1280, 720), surface)
    assert mapping_issue(observed) is None
    for aoi, (x, y) in POINTS.items():
        # Forward rendering uses the production renderer, not the gaze transform.
        nx = (view.x + x * view.width / 1280) / surface[0]
        ny = (view.y + y * view.height / 720) / surface[1]
        actual, point = classify_screen(nx, ny, observed, layout)
        assert actual == aoi
        assert point == pytest.approx({"x": x, "y": y}, abs=1e-9)
    if view.y:
        assert classify_screen(.5, 0, observed, layout) == ("NO_AOI", None)
    if view.x:
        assert classify_screen(0, .5, observed, layout) == ("NO_AOI", None)


@pytest.mark.parametrize("surface", [(1280, 720), (1920, 1080), (3840, 2160)])
def test_prechecked_physical_display_accepts_equivalent_render_scale(surface):
    assert mapping_issue(four_k(surface), mapping_signature(four_k())) is None


@pytest.mark.parametrize("change", [
    {"monitor_name": "DISPLAY3"}, {"dpi": 144}, {"surface_size": (1024, 768)},
    {"monitor": Rect(0, 0, 3840, 2160), "client": Rect(0, 0, 3840, 2160)},
])
def test_scaled_surface_fix_does_not_accept_different_display_or_aspect(change):
    assert mapping_issue(replace(four_k(), **change), mapping_signature(four_k()))


@pytest.mark.parametrize("module", ["M1", "M2"])
def test_module_precheck_accepts_4k_client_fhd_surface_and_records_both(tmp_path, module):
    check = M2GazeCheck(services(tmp_path), tmp_path / "config.json", DriverViewConfig(), module=module)
    check.attempt_dir = tmp_path
    check.segments = successful_attempt()["segments"]
    check.capture = SimpleNamespace(close=lambda: None, errors=[], thread=None,
                                   observation_drops=0, geometry=four_k(), tablet=None)
    check.finish_attempt()
    assert check.result["passed"] and check.result["errors"] == []
    assert check.result["display_mapping"]["monitor"]["width"] == 3840
    assert check.result["display_mapping"]["surface_size"] == [1920, 1080]


@pytest.mark.parametrize("module", ["M1", "M2"])
def test_scaled_runtime_manual_f11_roundtrip_and_monitor_drag_recover(tmp_path, module):
    svc = services(tmp_path)
    record_decision(svc, successful_attempt() | {"display_mapping": mapping_signature(four_k())},
                    continued_with_exclusions=False, module=module)

    class MovingProbe(FourKProbe):
        observed = four_k()
        def snapshot(self, hwnd, size):
            if self.observed == "spanning":
                raise WindowSpansMonitors("Client must fit entirely within one monitor")
            return self.observed

    item = PilotGaze(svc, Stage[f"MODULE_{module[-1]}_RUNNING"], DriverViewConfig(),
                     tmp_path / "gaze.json", probe_factory=MovingProbe)
    prepare(item)
    for observed in (
        four_k(), replace(four_k(), client=Rect(2000, 100, 1920, 1080)), "spanning",
        replace(four_k(), monitor_name="OTHER"), four_k((3840, 2160)),
    ):
        item.suspend_presentation()  # Existing renderer transition hook.
        MovingProbe.observed = observed
        item.presentation = (time.monotonic_ns(), 1, (1920, 1080))
        item.last_present_ns = time.monotonic_ns()
        item._sample(decoded(left=(.5, .4), right=(.5, .4)))
    rows = gaze_rows(item)
    assert [r["aoi"] for r in rows] == ["FORWARD", "NO_AOI", "NO_AOI", "NO_AOI", "FORWARD"]
    assert [r["mapping_reason"] for r in rows] == [
        None, "FULLSCREEN_MAPPING_REQUIRED", "WINDOW_SPANS_MONITORS", "TRACKER_MONITOR_MISMATCH", None,
    ]
    assert all(r["mapping_id"] is None for r in rows[1:4])
    assert rows[0]["mapping_id"] != rows[-1]["mapping_id"]
    assert not item.errors and not item.stop.is_set()
    assert all(item.gaze_quality["eligible_aois"].values())


def test_mapping_evidence_write_error_keeps_samples_unmapped_and_retries(tmp_path, monkeypatch):
    item = collector(tmp_path, probe_factory=FourKProbe)
    prepare(item)
    original = item._record
    failures = [True, True, False]
    def write(handle, row):
        if row["record_type"] == "mapping" and failures.pop(0):
            raise OSError("mapping evidence disk error")
        original(handle, row)
    monkeypatch.setattr(item, "_record", write)
    for _ in range(3):
        item._sample(decoded(left=(.5, .4), right=(.5, .4)))
    rows = gaze_rows(item)
    assert [r["aoi"] for r in rows] == ["NO_AOI", "NO_AOI", "FORWARD"]
    assert [r["mapping_id"] is None for r in rows] == [True, True, False]
    assert any("mapping evidence disk error" in error for error in item.errors)
    assert not item.stop.is_set()


def test_drag_spanning_monitors_retries_at_existing_probe_cadence(tmp_path, monkeypatch):
    now = [1_000_000_000]
    monkeypatch.setattr("src.p8.pilot_gaze.time.monotonic_ns", lambda: now[0])
    class DragProbe(FourKProbe):
        calls = 0
        def snapshot(self, hwnd, size):
            type(self).calls += 1
            if self.calls == 1:
                raise WindowSpansMonitors("Client must fit entirely within one monitor")
            return super().snapshot(hwnd, size)
    item = collector(tmp_path, probe_factory=DragProbe)
    prepare(item)
    assert item._mapping(now[0]) == "WINDOW_SPANS_MONITORS"
    now[0] += 100_000_000
    assert item._mapping(now[0]) == "WINDOW_SPANS_MONITORS" and DragProbe.calls == 1
    now[0] += 150_000_000
    assert item._mapping(now[0]) is None and DragProbe.calls == 2
    assert not item.errors


def test_exported_data_alone_identifies_four_aois_and_unknown_states(tmp_path):
    class RegionSource(Source):
        def subscribe(self, callback):
            self.record_sink(json.dumps(display_record()))
            points = [(x / 1280, y / 720) for x, y in POINTS.values()] + [(1.1, 1.2)]
            for i, point in enumerate(points):
                self.record_sink(json.dumps(record(left=point, right=point, index=i)))
                callback(decoded(left=point, right=point, index=i))
            callback(decoded(valid=(0, 0), index=4))
            callback(decoded(left=(.5, .9), right=(.5, .9), index=5))
            callback(decoded(left=(1.1, 1.2), right=(.5, .4), index=6))
            item.suspend_presentation()
            callback(decoded(left=(.5, .4), right=(.5, .4), index=7))

    item = collector(tmp_path, source_factory=RegionSource, probe_factory=FourKProbe)
    (tmp_path / "tablet.json").write_text(json.dumps(profile()))
    item.config_path.write_text(json.dumps({
        "tobii_python": "unused", "display_confirmed": True, "tablet_profile": "tablet.json",
    }))
    item.presentation = (time.monotonic_ns(), 1, (1920, 1080))
    item.last_present_ns = time.monotonic_ns()
    item.start()
    deadline = time.monotonic() + 3
    while item.counts["samples"] < 8 and time.monotonic() < deadline:
        time.sleep(.01)
    item.close()
    (item.directory / "session_manifest.json").write_text(json.dumps(
        {"state": "SESSION_COMPLETE", "run_id": item.directory.name}))
    result = export_operator_results(item.directory, closed=True)
    assert result.error is None
    with ZipFile(result.archive) as archive:
        name = next(n for n in archive.namelist() if n.endswith("/gaze.jsonl"))
        rows = list(map(json.loads, archive.read(name).decode().splitlines()))
        summary_name = next(n for n in archive.namelist() if n.endswith("/gaze/" + item.pending.name + "/summary.json"))
        summary = json.loads(archive.read(summary_name))
    assert summary["status"] == "COLLECTED" and summary["errors"] == []
    schema = next(r["aoi_schema"] for r in rows if r["record_type"] == "metadata")
    assert schema["aoi_labels"]["TABLET"] == "외부 태블릿"
    definitions = {r["mapping_id"]: r["definition"] for r in rows
                   if r["record_type"] == "mapping" and "definition" in r}
    tablet = next(r for r in rows if r["record_type"] == "tablet_profile")
    gazes = [r for r in rows if r["record_type"] == "gaze"]
    assert [r["aoi"] for r in gazes] == [*POINTS, "TABLET", *["NO_AOI"] * 4]
    assert [r["observation_state"] for r in gazes[4:]] == [
        "TRACKING_LOST", "VALID_UNMAPPED", "AOI_UNCERTAIN", "MAPPING_UNAVAILABLE",
    ]
    for row in gazes[:3]:
        definition = definitions[row["mapping_id"]]
        transform = definition["logical_to_display_normalized"]
        xy = row["screen_gaze_display_normalized"]
        logical = [(xy[axis] - transform["offset"][i]) / transform["scale"][i]
                   for i, axis in enumerate(("x", "y"))]
        assert logical == pytest.approx(POINTS[row["aoi"]])
        assert row["aoi"] in definition["screen_regions_logical"]
        assert "REAR_MIRROR" not in definition["screen_regions_logical"]
        if "rect" in definition["screen_regions_logical"][row["aoi"]]:
            rect = definition["screen_regions_logical"][row["aoi"]]["rect"]
            assert rect["x"] <= logical[0] < rect["x"] + rect["width"]
            assert rect["y"] <= logical[1] < rect["y"] + rect["height"]
    assert gazes[3]["tablet_profile_sha256"] == tablet["sha256"]
    assert tablet["profile"]["polygons"] and gazes[3]["tablet_eye_hits"] == {"left": True, "right": True}
    assert gazes[-1]["mapping_id"] is None
