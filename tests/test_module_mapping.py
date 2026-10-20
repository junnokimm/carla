from dataclasses import replace
from types import SimpleNamespace
import io
import json
import time

import pygame
import pytest

from src.p6.aoi import Rect
from src.p6.gaze_precheck import AOIS, stage_quality
from src.p8.m2_gaze_check import CheckCapture, M2GazeCheck, record_decision
from src.p8.model import Stage
from src.p8.operator_ui import OperatorApp
from src.p8.pilot_display import mapping_issue, mapping_signature
from src.p8.pilot_gaze import PilotGaze
from src.scenario.driver_view import DriverViewConfig
from src.scenario.driver_view_presentation import DriverViewPresentation
from tests.test_m2_gaze_precheck import services, successful_attempt
from tests.test_p8_operator_ui import FakeController, config
from tests.test_pilot_gaze import Probe, geometry, sample


@pytest.mark.parametrize("ready", [True, False])
def test_m1_check_precedes_drive_and_cancellation_is_nonfatal(monkeypatch, ready):
    controller = FakeController(Stage.MODULE_1_PREPARE)
    app = OperatorApp(controller, config(1.0))
    def check(actual, *, module):
        assert actual is controller and module == "M1"
        assert actual.stage is Stage.MODULE_1_PREPARE and not actual.run_stages
        return ready
    monkeypatch.setattr("src.p8.m2_gaze_check.prepare_module_gaze", check)
    app._handle_key(pygame.K_SPACE)
    assert bool(controller.run_stages) is ready
    assert not controller.abort_reasons


def test_attempts_and_passes_are_scoped_to_their_own_module(tmp_path):
    svc = services(tmp_path)
    decision = record_decision(svc, successful_attempt(), continued_with_exclusions=False, module="M1")
    svc.m1_gaze_attempts = [successful_attempt()] * 3
    assert stage_quality("MODULE_1_RUNNING", decision)["passed"]
    assert not any(stage_quality("MODULE_2_RUNNING", decision)["eligible_aois"].values())
    second = M2GazeCheck(svc, tmp_path / "config.json", DriverViewConfig())
    assert second.attempts == []
    assert (tmp_path / "m1_gaze_quality.json").exists()
    assert not hasattr(svc, "m2_gaze_quality")


def test_precheck_cannot_pass_without_observed_window_geometry(tmp_path):
    check = M2GazeCheck(services(tmp_path), tmp_path / "config.json", DriverViewConfig(), module="M1")
    check.attempt_dir = tmp_path
    check.segments = successful_attempt()["segments"]
    check.capture = SimpleNamespace(close=lambda: None, errors=[], thread=None,
                                     observation_drops=0, geometry=None, tablet=None)
    check.finish_attempt()
    assert not check.result["passed"]
    assert "PRECHECK_DISPLAY_MAPPING_MISSING" in check.result["errors"]


@pytest.mark.parametrize("module", ["M1", "M2"])
def test_runtime_mapping_change_preserves_raw_gaze_but_excludes_analysis(tmp_path, module):
    svc = services(tmp_path)
    decision = record_decision(svc, successful_attempt() | {"display_mapping": mapping_signature(geometry())},
                               continued_with_exclusions=False, module=module)
    class ChangedProbe(Probe):
        def snapshot(self, hwnd, size):
            return replace(geometry(), dpi=144)
    item = PilotGaze(svc, Stage[f"MODULE_{module[-1]}_RUNNING"], DriverViewConfig(), tmp_path / "gaze.json",
                     probe_factory=ChangedProbe)
    item.config = {"display_confirmed": True}
    item.gaze = item.wire = io.StringIO()
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item._sample(sample())
    rows = [json.loads(line) for line in item.gaze.getvalue().splitlines()]
    row = next(r for r in rows if r["record_type"] == "gaze")
    assert row["observation_state"] == "MAPPING_UNAVAILABLE"
    assert row["sample"]["source_metadata"]["raw_sdk_json"]
    assert not any(item.gaze_quality["eligible_aois"].values())
    assert all(decision["eligible_aois"].values())  # Never overwrite the original check.
    assert item.errors and not item.stop.is_set()


def test_recreated_window_accepts_same_mapping_but_changed_monitor_or_dpi_fails():
    original = geometry()
    expected = mapping_signature(original)
    assert mapping_issue(replace(original, hwnd=99), expected) is None
    assert mapping_issue(replace(original, monitor_name="DISPLAY2"), expected)
    assert mapping_issue(replace(original, dpi=144), expected)


def test_precheck_starts_windowed_and_cannot_measure_before_manual_f11(tmp_path, monkeypatch):
    check = M2GazeCheck(services(tmp_path), tmp_path / "config.json", DriverViewConfig())
    def events():
        assert not check.presentation._fullscreen
        check.handle_key(pygame.K_SPACE, time.perf_counter_ns())
        assert check.capture is None and check.mode == "INTRO"
        assert "F11" in check.status
        return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)]
    monkeypatch.setattr(pygame.event, "get", events)
    monkeypatch.setattr(DriverViewPresentation, "_toggle_fullscreen",
                        lambda self: pytest.fail("No automatic fullscreen transition"))
    assert check.run() is False


@pytest.mark.parametrize("module", ["M1", "M2"])
def test_windowed_start_and_manual_transition_preserve_mapping_eligibility(tmp_path, module):
    svc = services(tmp_path)
    record_decision(svc, successful_attempt() | {"display_mapping": mapping_signature(geometry())},
                    continued_with_exclusions=False, module=module)
    svc.pilot_gaze_monitor = {"name": "OTHER_STARTUP_MONITOR"}
    class MovingProbe(Probe):
        observed = replace(geometry(client=Rect(0, 0, 640, 360)), monitor_name="OTHER_STARTUP_MONITOR")
        def snapshot(self, hwnd, size):
            return self.observed
    item = PilotGaze(svc, Stage[f"MODULE_{module[-1]}_RUNNING"], DriverViewConfig(), tmp_path / "gaze.json",
                     probe_factory=MovingProbe)
    item.config = {"display_confirmed": True}
    item.gaze = item.wire = io.StringIO()
    # Initial window on another monitor, manually selected fullscreen, F11 roundtrip.
    windowed = MovingProbe.observed
    for observed in (windowed, geometry(), windowed, geometry()):
        MovingProbe.observed = observed
        item.geometry_key = None
        item.presentation = (time.monotonic_ns(), 1, observed.surface_size)
        item.last_present_ns = time.monotonic_ns()
        item._sample(sample())
    rows = [json.loads(line) for line in item.gaze.getvalue().splitlines()]
    rows = [r for r in rows if r["record_type"] == "gaze"]
    assert [r["observation_state"] for r in rows] == ["MAPPING_UNAVAILABLE", "FORWARD"] * 2
    assert all(r["sample"]["source_metadata"]["raw_sdk_json"] for r in rows)
    assert all(item.gaze_quality["eligible_aois"].values())
    assert item.mapping_checked and not item.errors and not item.stop.is_set()
    assert item.counts["mapping_reason:FULLSCREEN_MAPPING_REQUIRED"] == 2


@pytest.mark.parametrize("configured", [None, "OTHER_STARTUP_MONITOR"])
def test_manual_precheck_monitor_selection_respects_explicit_tracker_config(tmp_path, configured):
    svc = services(tmp_path)
    svc.pilot_gaze_monitor = {"name": "OTHER_STARTUP_MONITOR"}
    item = CheckCapture(svc, Stage.MODULE_2_PREPARE, DriverViewConfig(), tmp_path / "gaze.json",
                        probe_factory=Probe)
    item.config = {"display_confirmed": True, "monitor_name": configured}
    item.gaze = io.StringIO()
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    reason = item._mapping(time.monotonic_ns())
    assert reason == ("TRACKER_MONITOR_MISMATCH" if configured else None)
    assert not item.errors and not item.stop.is_set()
