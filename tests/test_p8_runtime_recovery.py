from types import SimpleNamespace

import pygame
import pytest

from src.p8.operator_recovery import OperatorRecovery
from src.p8.operator_ui_runtime import run_operator_ui
from src.p8.tsxw_input import TsxwDeviceError
from tests.test_p8_operator_ui import FakeController, config


def recovery_fixture():
    clock = SimpleNamespace(now=0.0)
    calls, records, events = [], [], []
    session = SimpleNamespace(
        request_control_mode=lambda mode: calls.append(("mode", mode.value)),
        hero=SimpleNamespace(apply_control=lambda c: calls.append(("brake", c.brake))),
        viewer=SimpleNamespace(recover_input=lambda: calls.append(("reopen", True))),
        persistence=SimpleNamespace(
            source=SimpleNamespace(
                get_observation=lambda: calls.append(("observe", True))
            ),
            flush_events=lambda: None,
        ),
    )
    services = SimpleNamespace(emit=lambda n, p: events.append((n, p)))
    recovery = OperatorRecovery(session, services, records, clock=lambda: clock.now)
    return recovery, clock, calls, records, events


@pytest.mark.parametrize(
    "error",
    [TsxwDeviceError("disconnected"), OSError("disk"), RuntimeError("RPC timeout")],
)
def test_runtime_fault_requires_confirmation_and_records_gap(monkeypatch, error):
    recovery, clock, calls, records, _ = recovery_fixture()
    scene = SimpleNamespace(
        started_at=1.0, invalidate=lambda e, p: calls.append(("invalid", p))
    )

    def confirm(record):
        assert record["status"] == "AWAITING_OPERATOR"
        clock.now += 15
        return True

    monkeypatch.setattr(recovery, "_wait_for_operator", confirm)
    with recovery.guard(scene):
        raise error
    assert not recovery.exit_requested
    assert recovery.paused_seconds == 15
    assert scene.started_at == 16
    assert records[0]["status"] == "RETRY_AUTHORIZED"
    assert ("mode", "MANUAL") in calls
    assert (("reopen", True) in calls) == isinstance(error, TsxwDeviceError)


def test_failed_retry_and_failed_logging_keep_waiting_until_explicit_stop(monkeypatch):
    recovery, _, _, records, _ = recovery_fixture()
    choices = iter((True, False))
    monkeypatch.setattr(recovery, "_wait_for_operator", lambda _: next(choices))

    def fail(*_):
        raise OSError("unavailable")

    recovery.services.emit = fail
    recovery.session.persistence.source.get_observation = fail
    with recovery.guard(None):
        raise RuntimeError("lost actor")
    assert recovery.exit_requested
    assert records[0]["status"] == "OPERATOR_REQUESTED_STOP"
    assert "retry_error" in records[0] and "recording_error" in records[0]


def test_keyboard_interrupt_is_not_swallowed():
    recovery, _, _, records, _ = recovery_fixture()
    with pytest.raises(KeyboardInterrupt), recovery.guard(None):
        raise KeyboardInterrupt
    assert not records


def test_operator_window_survives_training_error_until_explicit_quit(monkeypatch):
    controller = FakeController()

    def fail(*_):
        raise RuntimeError("setup failed")

    controller.execute_training = fail
    polls = []

    def events():
        assert not controller.abort_reasons
        polls.append(True)
        return [] if len(polls) == 1 else [pygame.event.Event(pygame.QUIT)]

    monkeypatch.setattr(pygame.event, "get", events)
    assert run_operator_ui(controller, config(1.0)) == 2
    assert len(polls) == 2
    assert controller.abort_reasons == ["OPERATOR_WINDOW_QUIT"]


def test_real_driver_loop_retries_without_closing_or_counting_pause(monkeypatch):
    from src.experiment.automation_interaction import DriverInput
    from src.scenario import driver_view
    from tests.test_driver_view import FakeHero, FakeWorld

    recovery, clock, calls, records, _ = recovery_fixture()
    view = driver_view.DriverView(FakeWorld(), FakeHero())
    view.operator_recovery = recovery
    trace = []
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda _: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda _: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: trace.append("flip"))
    monkeypatch.setattr(pygame, "quit", lambda: trace.append("quit"))
    monkeypatch.setattr(pygame.event, "get", list)
    monkeypatch.setattr(
        pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda _: None)
    )
    monkeypatch.setattr(
        driver_view,
        "DriverViewPresentation",
        lambda *args: SimpleNamespace(
            canvas=object(),
            handle_events=lambda _: None,
            present=lambda: None,
        ),
    )
    monkeypatch.setattr(view, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(view, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(
        view, "_draw", lambda _: setattr(clock, "now", clock.now + 0.02)
    )
    polls = []

    def collect(_):
        polls.append(True)
        if len(polls) == 1:
            raise TsxwDeviceError("disconnected")
        return DriverInput()

    def confirm(_):
        assert "quit" not in trace
        clock.now += 10
        return True

    monkeypatch.setattr(view, "_collect_driver_input", collect)
    monkeypatch.setattr(recovery, "_wait_for_operator", confirm)
    scene = SimpleNamespace(
        started_at=0.0,
        invalidate=lambda *args: calls.append(("invalid", True)),
        after_control=lambda *args: None,
        after_display_completed=lambda _: None,
    )
    assert view.run(0.04, active_scene=scene) is False
    assert len(polls) >= 3
    assert records[0]["paused_seconds"] == 10
    assert scene.started_at == 10
    assert trace.count("quit") == 1 and trace[-1] == "quit"
