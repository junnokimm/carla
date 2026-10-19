import json
from types import SimpleNamespace

import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.scenario import driver_view
from src.scenario.loop_timing_distribution import LoopTimingDistribution
from src.scenario.research_camera_diagnostics import ResearchCameraDiagnostics
from tests.test_driver_view import FakeHero, FakeWorld


@pytest.mark.parametrize(
    "fail_at", [None, "active_scene_after_control", "active_scene_after_display"]
)
def test_scene_spans_measure_work_and_preserve_failure(monkeypatch, fail_at):
    clock = SimpleNamespace(now=0.0)
    trace = []
    diagnostics = ResearchCameraDiagnostics(lambda: clock.now)
    view = driver_view.DriverView(FakeWorld(), FakeHero())
    view._performance_observer = diagnostics
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda text: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: trace.append("flip"))
    monkeypatch.setattr(pygame, "quit", lambda: trace.append("quit"))
    monkeypatch.setattr(pygame.event, "get", list)
    monkeypatch.setattr(
        pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda fps: None)
    )
    monkeypatch.setattr(
        driver_view,
        "DriverViewPresentation",
        lambda *args: SimpleNamespace(
            canvas=object(), handle_events=lambda events: None, present=lambda: None
        ),
    )
    monkeypatch.setattr(view, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(view, "_collect_driver_input", lambda events: DriverInput())
    monkeypatch.setattr(view, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(view, "_draw", lambda screen: None)

    def work(name, duration):
        trace.append(name)
        clock.now += duration
        if name == fail_at:
            raise RuntimeError(name)

    scene = SimpleNamespace(
        started_at=0,
        after_control=lambda *args: work("active_scene_after_control", 0.03),
        after_display_completed=lambda frame: work("active_scene_after_display", 0.02),
    )
    if fail_at:
        with pytest.raises(RuntimeError, match=fail_at):
            view.run(0.04, active_scene=scene)
    else:
        view.run(0.04, active_scene=scene)
    result = json.loads(diagnostics.to_json([]))
    assert result["timing"]["active_scene_after_control"]["mean_ms"] == 30
    if fail_at != "active_scene_after_control":
        assert result["timing"]["active_scene_after_display"]["mean_ms"] == 20
        assert trace.index("flip") < trace.index("active_scene_after_display")
    assert trace[-1] == "quit"


def test_loop_percentile_bounds_are_bounded_by_mode_and_exclude_partial_loops():
    mode = DrivingControlMode.MANUAL
    diagnostics = ResearchCameraDiagnostics(control_mode_source=lambda: mode)
    diagnostics.begin_active_window([])
    for value in range(1, 101):
        diagnostics.begin_driver_loop()
        diagnostics.record_duration("driver_loop", value / 1000)
    mode = DrivingControlMode.NOA_ACTIVE
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("driver_loop", 2)
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("active_scene_after_control", 5)
    diagnostics.end_active_window([], complete=False)
    diagnostics.record_duration("driver_loop", 9)
    result = json.loads(diagnostics.to_json([]))["completed_loop_distribution"]
    manual = result["by_mode"]["MANUAL"]
    assert manual["count"] == 100
    assert manual["p95_ms_bounds"][0] <= 95 <= manual["p95_ms_bounds"][1]
    assert manual["p99_ms_bounds"][0] <= 99 <= manual["p99_ms_bounds"][1]
    assert result["all_modes"]["count"] == 101
    assert result["by_mode"]["NOA_ACTIVE"]["p99_ms_bounds"] == [1000, 2000]
    diagnostics.begin_active_window([])
    assert (
        json.loads(diagnostics.to_json([]))["completed_loop_distribution"]["all_modes"][
            "count"
        ]
        == 0
    )


def test_histogram_storage_is_fixed_and_missing_values_are_not_zero_samples():
    stats = LoopTimingDistribution()
    for value in [float("nan"), float("inf"), -1]:
        stats.record(value)
    assert stats.payload() == {"count": 0, "p95_ms_bounds": None, "p99_ms_bounds": None}
    for _ in range(10000):
        stats.record(0.0205)
    assert len(stats.bins) == 1001
    assert sum(stats.bins) == stats.count == 10000
    assert stats.payload()["p99_ms_bounds"] == [20, 20.5]


def test_scene_timings_join_same_loop_correlation():
    diagnostics = ResearchCameraDiagnostics()
    diagnostics.begin_active_window([])
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("active_scene_after_control", 0.03)
    diagnostics.record_duration("active_scene_after_display", 0.02)
    diagnostics.record_duration("driver_loop", 0.06)
    result = json.loads(diagnostics.to_json([]))["slowest_completed_loop"]
    assert result["segments"]["active_scene_after_control"]["mean_ms"] == 30
    assert result["segments"]["active_scene_after_display"]["mean_ms"] == 20
