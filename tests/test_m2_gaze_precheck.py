from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pygame
import pytest

from src.p6.gaze_precheck import (
    AOIS,
    analysis_seconds,
    evaluate_attempt,
    evaluate_segment,
    stage_quality,
)
from src.p8.m2_gaze_check import M2GazeCheck, record_decision
from src.p8.model import Stage
from src.p8.operator_ui import OperatorApp
from src.scenario.driver_view import DriverViewConfig
from tests.test_p8_operator_ui import FakeController, config


def samples(target="TABLET", n=300):
    return [{"aoi": target, "sdk_tracked_eyes": ["left"], "observation_state": target,
             "duration_callback_perf_counter_ns": int((i + .5) * 5e9 / n)} for i in range(n)]


def segment(target="TABLET", rows=None):
    return evaluate_segment(target, samples(target) if rows is None else rows, 0, 5_000_000_000)


def successful_attempt():
    return evaluate_attempt([segment(a) for a in (*AOIS, "FORWARD")])


def services(tmp_path):
    return SimpleNamespace(directory=tmp_path, context=SimpleNamespace(study_run_id="RUN", participant_id="P1"),
                           clock=SimpleNamespace(origin=SimpleNamespace(monotonic_ns=time.monotonic_ns())),
                           emit=lambda *args: None)


def test_valid_monocular_data_passes_but_missing_callbacks_do_not():
    assert segment()["passed"]
    assert not segment(rows=samples(n=30))["passed"]
    assert "INSUFFICIENT_CALLBACK_COVERAGE" in segment(rows=samples(n=30))["reasons"]
    assert segment(rows=[])["tracking_rate"] == 0
    assert segment(rows=[])["max_untracked_gap_seconds"] == 5


def test_loss_is_in_denominator_and_long_loss_fails_even_good_average():
    rows = samples()
    for row in rows[100:136]:  # 88% averages would pass; a 0.6 s loss must not.
        row.update(aoi="NO_AOI", sdk_tracked_eyes=[], observation_state="TRACKING_LOST")
    result = segment(rows=rows)
    assert result["tracking_rate"] == result["target_rate"] == .88
    assert "LONG_TRACKING_OR_CALLBACK_LOSS" in result["reasons"]
    assert not result["passed"]
    rows = samples()
    for i in range(0, 300, 5):
        rows[i].update(aoi="NO_AOI", sdk_tracked_eyes=[], observation_state="TRACKING_LOST")
    assert "LOW_TRACKING_RATE" in segment(rows=rows)["reasons"]


def test_cross_aoi_confusion_excludes_both_source_and_destination():
    rows = samples("FORWARD")
    for row in rows[:30]:
        row["aoi"] = "TABLET"
    result = evaluate_attempt([segment(a) if a != "FORWARD" else segment(a, rows) for a in AOIS])
    assert set(result["excluded_aois"]) == {"FORWARD", "TABLET"}
    assert result["eligible_aois"]["LEFT_MIRROR"]


def test_final_forward_check_is_required_by_completed_ui_and_cannot_hide_failure():
    assert not evaluate_attempt([segment(a) for a in AOIS])["eligible_aois"]["FORWARD"]
    results = [segment(a) for a in (*AOIS, "FORWARD")]
    results[-1] = segment("FORWARD", samples("NO_AOI"))
    assert not evaluate_attempt(results)["eligible_aois"]["FORWARD"]
    assert successful_attempt()["passed"]


def test_excluded_time_is_null_not_zero_and_other_stages_are_not_certified():
    decision = successful_attempt()
    decision["eligible_aois"]["TABLET"] = False
    duration = {"seconds_by_state": {"TABLET_MONOCULAR": 12.5, "FORWARD": 20.0}}
    output = analysis_seconds(duration, stage_quality("MODULE_2_RUNNING", decision))
    assert output["TABLET"] is None and output["FORWARD"] == 20
    assert duration["seconds_by_state"]["TABLET_MONOCULAR"] == 12.5
    assert all(v is None for v in analysis_seconds(duration, stage_quality("TRAINING", decision)).values())
    assert not any(stage_quality("MODULE_2_RUNNING", None)["eligible_aois"].values())


def test_failure_requires_explicit_two_key_continue_and_preserves_decision(tmp_path):
    svc = services(tmp_path)
    check = M2GazeCheck(svc, tmp_path / "config.json", DriverViewConfig())
    check.mode = "RESULT"
    check.result = evaluate_attempt([])
    assert check.handle_key(pygame.K_SPACE, 0) is None
    assert check.handle_key(pygame.K_RETURN, 0) is None
    assert not hasattr(svc, "m2_gaze_quality")
    assert check.handle_key(pygame.K_F8, 0) is None
    assert check.handle_key(pygame.K_RETURN, 0) is True
    saved = json.loads((tmp_path / "m2_gaze_quality.json").read_text(encoding="utf-8"))
    assert saved["status"] == "CONTINUE_WITH_EXCLUSIONS"
    assert saved["excluded_aois"] == list(AOIS)
    assert saved["calibration_performed"] is False


def test_retry_limit_survives_return_to_operator_screen_and_does_not_reuse_old_pass(tmp_path):
    svc = services(tmp_path)
    svc.m2_gaze_attempts = [successful_attempt(), evaluate_attempt([]), evaluate_attempt([])]

    def forbidden(*args, **kwargs):
        pytest.fail("Must not start a fourth capture")

    check = M2GazeCheck(svc, tmp_path / "config.json", DriverViewConfig(), capture_factory=forbidden)
    assert not check.result["passed"]
    check.handle_key(pygame.K_r, 0)
    assert check.capture is None
    assert check.handle_key(pygame.K_ESCAPE, 0) is False
    assert not hasattr(svc, "m2_gaze_quality")


def test_missing_sdk_or_config_is_visible_and_all_areas_excluded(tmp_path):
    def failed(*args, **kwargs):
        raise RuntimeError("SDK_MISSING")

    check = M2GazeCheck(services(tmp_path), tmp_path / "missing.json", DriverViewConfig(), capture_factory=failed)
    check.begin_attempt()
    assert check.mode == "RESULT"
    assert not check.result["passed"]
    assert "SDK_MISSING" in check.result["errors"][0]
    assert check.handle_key(pygame.K_ESCAPE, 0) is False


@pytest.mark.parametrize("ready", [False, True])
def test_m2_gate_precedes_transition_and_never_runs_m2_on_cancel(monkeypatch, ready):
    controller = FakeController(Stage.MODULE_2_PREPARE)
    app = OperatorApp(controller, config(1.0))
    app.registered = True

    def check(_controller):
        assert _controller.stage is Stage.MODULE_2_PREPARE
        assert not controller.run_stages
        return ready

    monkeypatch.setattr("src.p8.m2_gaze_check.prepare_m2_gaze", check)
    app._handle_key(pygame.K_SPACE)
    assert bool(controller.run_stages) is ready
    assert not controller.abort_reasons


def test_windowed_mode_cannot_start_measurement(tmp_path):
    check = M2GazeCheck(services(tmp_path), tmp_path / "config.json", DriverViewConfig())
    check.presentation = SimpleNamespace(_fullscreen=False)
    check.mode = "TARGET"
    check.handle_key(pygame.K_SPACE, 0)
    assert check.mode == "TARGET"
    assert "F11" in check.status


def test_explicitly_disabled_gaze_does_not_start_check(tmp_path, monkeypatch):
    from src.p8.m2_gaze_check import prepare_m2_gaze

    path = tmp_path / "gaze.json"
    path.write_text('{"enabled":false}')
    controller = SimpleNamespace(runtime=SimpleNamespace(gaze_config=path))
    monkeypatch.setattr(M2GazeCheck, "run", lambda _: pytest.fail("Gaze was explicitly disabled"))
    assert prepare_m2_gaze(controller) is True


def test_decision_disk_failure_keeps_in_memory_exclusions(tmp_path, monkeypatch):
    svc = services(tmp_path)

    def failed(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("src.p8.m2_gaze_check.save_json", failed)
    decision = record_decision(svc, evaluate_attempt([]), continued_with_exclusions=True)
    assert svc.m2_gaze_quality is decision
    assert not any(decision["eligible_aois"].values())
    assert "decision_storage_error" in decision


def test_precheck_capture_queue_overflow_is_counted_not_raised(tmp_path):
    import io
    import queue

    from src.p8.m2_gaze_check import CheckCapture

    item = CheckCapture(services(tmp_path), Stage.MODULE_2_PREPARE, DriverViewConfig(), tmp_path / "config.json")
    item.observations = queue.Queue(maxsize=1)
    output = io.StringIO()
    item._record(output, {"record_type": "gaze", "aoi": "FORWARD"})
    item._record(output, {"record_type": "gaze", "aoi": "FORWARD"})
    assert item.observation_drops == 1
    assert len(output.getvalue().splitlines()) == 2


def test_optional_ui_loop_failure_allows_explicit_continue_without_pygame_quit(tmp_path, monkeypatch):
    from src.scenario.driver_view_presentation import DriverViewPresentation

    def failed(*args, **kwargs):
        raise RuntimeError("SDK_NOT_AVAILABLE")

    def toggle(presentation):
        presentation._fullscreen = not presentation._fullscreen

    events = iter([[pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE),
                    pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)],
                   [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)],
                   [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8)],
                   [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)]])
    monkeypatch.setattr(pygame.event, "get", lambda: next(events))
    monkeypatch.setattr(DriverViewPresentation, "_toggle_fullscreen", toggle)
    svc = services(tmp_path)
    check = M2GazeCheck(svc, tmp_path / "missing.json", DriverViewConfig(), capture_factory=failed)
    assert check.run() is True
    assert pygame.display.get_init()
    assert svc.m2_gaze_presentations[-1] is check.presentation
    assert check.presentation.gaze_observer is None
    assert not svc.m2_gaze_quality["passed"]


def test_target_timing_ignores_settle_rows_and_passes_only_after_all_targets(tmp_path):
    from src.p6.pilot_aoi import layout_for_view
    from src.p8.m2_gaze_check import TARGETS

    class Capture:
        def __init__(self, *args, **kwargs):
            self.alive = True
            self.thread = SimpleNamespace(is_alive=lambda: self.alive)
            self.errors, self.batch = [], []
            self.tablet = None
            self.observation_drops = 0
            self.layout = layout_for_view(DriverViewConfig())
            from tests.test_pilot_gaze import geometry
            self.geometry = geometry()

        def start(self):
            self.batch = samples(n=1)

        def close(self):
            self.alive = False

        def drain(self):
            rows, self.batch = self.batch, []
            return rows

    svc = services(tmp_path)
    check = M2GazeCheck(svc, tmp_path / "config.json", DriverViewConfig(), capture_factory=Capture)
    check.begin_attempt()
    check.tick(time.perf_counter_ns())
    assert check.mode == "TARGET"
    for target in TARGETS:
        now = time.perf_counter_ns()
        check.handle_key(pygame.K_SPACE, now)
        assert check.mode == "MEASURING"
        rows = samples(target)
        for row in rows:
            row["duration_callback_perf_counter_ns"] += check.capture_start
        rows.insert(0, {**rows[0], "aoi": "NO_AOI", "duration_callback_perf_counter_ns": now})
        check.capture.batch = rows
        check.tick(check.capture_end + 200_000_000)
    assert check.mode == "RESULT" and check.result["passed"]
    assert all(s["sample_count"] == 300 for s in check.result["segments"])
    assert not hasattr(svc, "m2_gaze_quality")
    assert check.handle_key(pygame.K_SPACE, time.perf_counter_ns()) is True
    assert svc.m2_gaze_quality["status"] == "PASSED"


def test_excluded_tablet_preserves_raw_records_but_m2_analysis_is_null(tmp_path):
    from src.p6.tobii.protocol import TobiiSampleDecoder
    from tests.test_external_tablet_gaze import display_record, profile, record
    from tests.test_pilot_gaze import Probe, Source, collector

    class TabletSource(Source):
        def subscribe(self, callback):
            self.record_sink(json.dumps(display_record()))
            for index in range(5):
                row = record(index=index, valid=(1, 0))
                self.record_sink(json.dumps(row))
                callback(TobiiSampleDecoder().decode(json.dumps(row)).sample)
                time.sleep(.017)

    item = collector(tmp_path, source_factory=TabletSource, probe_factory=Probe)
    item.stage = "MODULE_2_RUNNING"
    item.gaze_quality = evaluate_attempt([])
    (tmp_path / "tablet.json").write_text(json.dumps(profile()))
    item.config_path.write_text(json.dumps({"tobii_python": "unused", "display_confirmed": True,
                                           "tablet_profile": "tablet.json"}))
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item.start()
    deadline = time.monotonic() + 3
    while item.counts["samples"] < 5 and time.monotonic() < deadline:
        time.sleep(.01)
    item.close()
    assert not item.thread.is_alive()
    root = next((item.directory / "gaze").iterdir())
    summary = json.loads((root / "summary.json").read_text())
    assert summary["duration"]["tablet_classified_seconds"] > 0
    assert summary["analysis_seconds_by_aoi"]["TABLET"] is None
    rows = [json.loads(line) for line in (root / "gaze.jsonl").read_text().splitlines()]
    gaze = [r for r in rows if r["record_type"] == "gaze"]
    assert len(gaze) == 5 and all(r["aoi"] == "TABLET" and r["analysis_aoi_included"] is False for r in gaze)
    totals = json.loads((item.directory / "gaze_totals.json").read_text())
    assert totals["tablet_classified_seconds"] > 0
    assert totals["m2_analysis_seconds_by_aoi"]["TABLET"] is None
