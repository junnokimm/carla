from types import SimpleNamespace

import pygame
import pytest

from src.experiment.automation_interaction import DriverInput
from src.scenario import driver_view
from src.scenario.camera_feed import CameraFeed
from tests.test_driver_view import FakeHero, FakeWorld


@pytest.mark.parametrize("flip_fails", [False, True])
def test_real_view_continuation_reports_selected_front_frame_only_after_successful_flip(
    monkeypatch, flip_fails
):
    clock = SimpleNamespace(now=0.0)
    trace = []
    reported = []

    def image(frame):
        return SimpleNamespace(
            frame=frame, timestamp=frame / 10, width=1, height=1, raw_data=bytes(4)
        )

    feed = CameraFeed("front", SimpleNamespace(id=123))
    feed.receipt_clock = lambda: clock.now + 100
    feed.receive(image(10))

    def blit(surface, position):
        trace.append("blit")
        feed.receive(image(feed.snapshot().last_sensor_frame + 10))

    canvas = SimpleNamespace(blit=blit)
    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(pygame.event, "get", list)
    monkeypatch.setattr(pygame, "quit", lambda: trace.append("quit"))
    monkeypatch.setattr(pygame.image, "frombuffer", lambda *args: object())
    monkeypatch.setattr(pygame.transform, "smoothscale", lambda *args: object())
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(
        driver_view.time, "monotonic_ns", lambda: int(clock.now * 1e9) + 100
    )
    monkeypatch.setattr(
        pygame.time,
        "Clock",
        lambda: SimpleNamespace(tick=lambda fps: setattr(clock, "now", clock.now + 1)),
    )
    monkeypatch.setattr(
        driver_view,
        "DriverViewPresentation",
        lambda *args: SimpleNamespace(
            canvas=canvas,
            handle_events=lambda events: None,
            present=lambda: trace.append("present"),
        ),
    )

    def flip():
        if flip_fails and clock.now >= 1:
            raise ValueError("original flip failure")
        trace.append("flip")

    monkeypatch.setattr(pygame.display, "flip", flip)
    view = driver_view.DriverView(FakeWorld(), FakeHero())
    monkeypatch.setattr(view, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(view, "_collect_driver_input", lambda events: DriverInput())
    monkeypatch.setattr(view, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(
        view, "_draw", lambda screen: view._blit_image(screen, feed, (1, 1), (0, 0))
    )

    def displayed(frame):
        assert trace[-1] == "flip"
        reported.append(frame)
        trace.append("display_report")
        return False

    continuation = SimpleNamespace(
        start=lambda: trace.append("start"),
        after_control_applied=lambda driver: True,
        after_display_completed=displayed,
        finalize=lambda reason, error=None: trace.append(reason),
    )
    if flip_fails:
        with pytest.raises(ValueError, match="original flip failure"):
            view.run(1, continuation_factory=lambda: continuation)
        assert reported == [] and "ERROR" in trace
    else:
        view.run(1, continuation_factory=lambda: continuation)
        assert len(reported) == 1
        assert reported[0].frame == 20 and feed.snapshot().last_sensor_frame == 30
        assert reported[0].sensor_actor_id == 123
        assert reported[0].simulation_seconds == 2
        assert reported[0].flip_completed_monotonic_ns == 1_000_000_100
        assert "TERMINAL" in trace
    assert trace[-1] == "quit"


@pytest.mark.parametrize(
    "event",
    [
        SimpleNamespace(type=pygame.QUIT),
        SimpleNamespace(type=pygame.KEYDOWN, key=pygame.K_ESCAPE),
    ],
)
def test_user_exit_queued_at_nominal_boundary_prevents_hazard_acquisition(
    monkeypatch, event
):
    from src.scenario.driver_view import DriverView

    clock = SimpleNamespace(now=0.0)
    batches = iter([[], [event]])
    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: None)
    monkeypatch.setattr(pygame, "quit", lambda: None)
    monkeypatch.setattr(pygame.event, "get", lambda: next(batches))
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(
        pygame.time,
        "Clock",
        lambda: SimpleNamespace(
            tick=lambda fps: setattr(clock, "now", clock.now + 780)
        ),
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
    view = DriverView(FakeWorld(), FakeHero())
    monkeypatch.setattr(view, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(view, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(view, "_draw", lambda screen: None)

    def forbidden():
        pytest.fail("user-aborted M2 must not acquire FINAL_HAZARD resources")

    assert view.run(780, continuation_factory=forbidden) is True
