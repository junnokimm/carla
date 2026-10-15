import json
from types import SimpleNamespace

import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_activation_gate import AutomationActivationGate
from src.experiment.automation_interaction import DriverInput
from src.scenario import driver_view
from src.scenario.research_camera_diagnostics import ResearchCameraDiagnostics
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.test_automation_interaction import FakeBoundingBox, FakeExtent, geometry
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_p5_exit_assistance import MANUAL
from tests.test_p8_phase2_performance import Clock, config


def payload(diagnostics):
    return json.loads(diagnostics.to_json([]))


def test_slowest_loop_correlates_same_loop_not_independent_segment_maxima():
    mode = DrivingControlMode.MANUAL
    diagnostics = ResearchCameraDiagnostics(control_mode_source=lambda: mode)
    diagnostics.begin_active_window([])
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("post_control", 0.12)
    diagnostics.record_duration("audio", 0.03)
    diagnostics.record_duration("driver_loop", 0.2)
    mode = DrivingControlMode.NOA_ACTIVE
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("event_poll_and_presentation", 1.1)
    diagnostics.record_duration("post_control", 0.04)
    diagnostics.record_duration("driver_loop", 1.2)
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("audio", 0.9)
    diagnostics.record_duration("driver_loop", 1.0)
    result = payload(diagnostics)
    worst = result["slowest_completed_loop"]
    assert worst["completed_loop_index"] == 2
    assert worst["duration_ms"] == pytest.approx(1200)
    assert worst["mode_at_completion"] == "NOA_ACTIVE"
    assert worst["segments"]["post_control"]["max_ms"] == 40
    assert "audio" not in worst["segments"]  # Not the 900ms from another loop.
    assert result["timing"]["post_control"]["max_ms"] == 120
    assert result["loop_correlation"]["completed_loop_count"] == 3


@pytest.mark.parametrize("ending", ["end", "freeze"])
def test_partial_loops_cleanup_and_continuation_cannot_replace_active_worst(ending):
    diagnostics = ResearchCameraDiagnostics()
    diagnostics.begin_active_window([])
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("driver_loop", 0.1)
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("event_poll_and_presentation", 5)
    if ending == "freeze":
        diagnostics.freeze_measurement_window([])
    else:
        diagnostics.end_active_window([], complete=False)
    diagnostics.begin_driver_loop()
    diagnostics.record_duration("driver_loop", 9)
    result = payload(diagnostics)
    assert result["loop_correlation"]["completed_loop_count"] == 1
    assert result["slowest_completed_loop"]["duration_ms"] == 100
    assert diagnostics._loop_segments is None


def test_loop_summary_is_bounded_and_resets_at_new_active_window():
    diagnostics = ResearchCameraDiagnostics()
    diagnostics.begin_active_window([])
    for _ in range(1000):
        diagnostics.begin_driver_loop()
        diagnostics.record_duration("draw", 0.01)
        diagnostics.record_duration("conversion.front", 0.001)
        diagnostics.record_duration("driver_loop", 0.1)
    assert set(payload(diagnostics)["slowest_completed_loop"]["segments"]) == {"draw"}
    assert diagnostics._loop_segments is None
    diagnostics.end_active_window([], complete=True)
    diagnostics.begin_active_window([])
    assert payload(diagnostics)["slowest_completed_loop"] is None
    assert payload(diagnostics)["loop_correlation"]["completed_loop_count"] == 0


@pytest.mark.parametrize(
    "site",
    [
        "event_poll_and_presentation",
        "mode_events",
        "frame_prepare",
        "after_display_flip",
    ],
)
@pytest.mark.parametrize("fail", [False, True])
def test_actual_loop_attributes_new_spans_and_preserves_errors_order_and_cleanup(
    monkeypatch, site, fail
):
    clock = Clock()
    trace = []

    def action(name):
        trace.append(name)
        clock.advance(1.2 if name == site else 0.001)
        if name == site and fail:
            raise RuntimeError(f"original {site} failure")

    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda name: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: action("flip"))
    monkeypatch.setattr(pygame, "quit", lambda: trace.append("quit"))
    monkeypatch.setattr(driver_view.time, "monotonic", clock)
    monkeypatch.setattr(
        pygame.time,
        "Clock",
        lambda: SimpleNamespace(tick=lambda fps: action("limiter")),
    )
    monkeypatch.setattr(
        driver_view,
        "DriverViewPresentation",
        lambda *args: SimpleNamespace(
            canvas=object(),
            handle_events=lambda events: None,
            present=lambda: None,
        ),
    )
    monkeypatch.setattr(
        pygame.event, "get", lambda: action("event_poll_and_presentation") or []
    )
    view = ResearchLiveDriverView(
        FakeWorld(),
        FakeHero(),
        ResearchDriverViewConfig(
            automation_state_source=lambda: MANUAL,
            manual_control_limits=ResearchManualControlLimits(1, 1, 1),
            control_sender=lambda control: action("checked_sender"),
        ),
    )
    diagnostics = ResearchCameraDiagnostics(
        clock, control_mode_source=lambda: MANUAL.control_mode
    )
    view._camera_diagnostics = diagnostics
    view._performance_observer = diagnostics
    view._turn_signal_audio = SimpleNamespace(close=lambda: trace.append("click.close"))
    monkeypatch.setattr(view, "_close_input", lambda: trace.append("input.close"))
    monkeypatch.setattr(view, "_collect_driver_input", lambda events: DriverInput())
    monkeypatch.setattr(
        view, "_handle_mode_events", lambda events: action("mode_events")
    )
    monkeypatch.setattr(view, "_prepare_frame", lambda: action("frame_prepare"))
    monkeypatch.setattr(
        view, "_after_display_flip", lambda: action("after_display_flip")
    )
    monkeypatch.setattr(view, "_draw", lambda canvas: action("draw"))
    monkeypatch.setattr(view, "_update_turn_signal_audio", lambda: action("audio"))
    observer = SimpleNamespace(
        update=lambda sample: action("input"),
        after_control_applied=lambda: action("post_control"),
    )
    if fail:
        with pytest.raises(RuntimeError, match=f"original {site} failure"):
            view.run(0.3, input_observer=observer)
    else:
        assert view.run(0.3, input_observer=observer) is False
    result = payload(diagnostics)
    assert result["timing"][site]["count"] == 1
    assert result["timing"][site]["max_ms"] == pytest.approx(1200)
    assert trace[-3:] == ["click.close", "input.close", "quit"]
    if fail:
        assert result["slowest_completed_loop"] is None
    else:
        assert result["slowest_completed_loop"]["segments"][site][
            "max_ms"
        ] == pytest.approx(1200)
        assert trace == [
            "event_poll_and_presentation",
            "input",
            "checked_sender",
            "mode_events",
            "post_control",
            "audio",
            "frame_prepare",
            "draw",
            "flip",
            "after_display_flip",
            "limiter",
            "click.close",
            "input.close",
            "quit",
        ]


def test_bbox_zero_extent_error_and_recovery_are_observed_without_cached_success():
    boxes = iter(
        [
            FakeBoundingBox(extent=FakeExtent(2, 2)),
            FakeBoundingBox(extent=FakeExtent(0, 0)),
            RuntimeError("actor unavailable"),
            FakeBoundingBox(extent=FakeExtent(2, 2)),
        ]
    )

    class Vehicle:
        reads = 0

        @property
        def bounding_box(self):
            self.reads += 1
            value = next(boxes)
            if isinstance(value, Exception):
                raise value
            return value

    hero = Vehicle()
    gate = AutomationActivationGate(
        SimpleNamespace(observe_with_context=lambda hero: geometry()), hero, config()
    )
    assert gate.failure(DriverInput()).value == "VEHICLE_STRADDLING"
    assert (
        gate.failure(DriverInput()) is None
    )  # Preserve existing zero-extent behavior.
    with pytest.raises(RuntimeError, match="actor unavailable"):
        gate.failure(DriverInput())
    assert gate.failure(DriverInput()).value == "VEHICLE_STRADDLING"
    assert hero.reads == 4
