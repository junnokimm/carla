from __future__ import annotations

from collections import defaultdict

import pytest

from src.scenario import driver_view
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_driver_view_runtime import FakeFrameClock


@pytest.mark.parametrize("exit_kind", ["USER_EXIT", "ERROR"])
def test_active_continuation_finalizes_once_on_nonterminal_exit(
    monkeypatch: pytest.MonkeyPatch,
    exit_kind: str,
) -> None:
    trace: list[str] = []
    times = iter((0.0, 0.0, 1.0))
    exit_event = driver_view.pygame.event.Event(
        driver_view.pygame.KEYDOWN, key=driver_view.pygame.K_ESCAPE
    )
    event_batches = iter(([], [exit_event] if exit_kind == "USER_EXIT" else []))
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", lambda: next(event_batches))
    monkeypatch.setattr(driver_view.pygame.time, "Clock", FakeFrameClock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: trace.append("quit"))
    monkeypatch.setattr(
        driver_view.pygame.key, "get_pressed", lambda: defaultdict(bool)
    )

    class Scheduler:
        def update(self) -> bool:
            return True

    class Continuation:
        def start(self) -> None:
            return

        def after_control_applied(self, driver_input) -> bool:
            if exit_kind == "ERROR":
                raise RuntimeError("continuation frame failed")
            return True

        def finalize(self, reason: str, error=None) -> None:
            trace.append(f"finalize:{reason}")

    viewer = driver_view.DriverView(FakeWorld(), FakeHero())
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(viewer, "_draw", lambda screen: None)

    if exit_kind == "ERROR":
        with pytest.raises(RuntimeError, match="continuation frame failed"):
            viewer.run(1.0, Scheduler(), continuation_factory=lambda: Continuation())
        assert trace == ["finalize:ERROR", "quit"]
    else:
        assert viewer.run(1.0, Scheduler(), continuation_factory=lambda: Continuation())
        assert trace == ["finalize:USER_EXIT", "quit"]
