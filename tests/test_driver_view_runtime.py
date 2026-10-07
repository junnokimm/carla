from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import carla
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_scheduler import AutomationControlScheduler
from src.experiment.noa_control import NoAControlCommand
from src.scenario.driver_view import DriverViewConfig
from src.vehicle.driving_mode import DrivingMode
from tests.test_driver_view import FakeHero, FakeWorld


@dataclass(frozen=True, slots=True)
class FixedAutomationRuntime:
    state: AutomationState


class RecordingStepBackend:
    def __init__(self, hero: FakeHero) -> None:
        self._hero = hero
        self.call_count = 0

    def step(self) -> NoAControlCommand:
        self.call_count += 1
        command = NoAControlCommand(throttle=0.2, brake=0.0, steering=-0.1)
        self._hero.apply_control(
            carla.VehicleControl(
                throttle=command.throttle,
                brake=command.brake,
                steer=command.steering,
            )
        )
        return command


class FakeTurnSignalAudio:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class FakeFrameClock:
    def __init__(self) -> None:
        self.tick_rates: list[int] = []

    def tick(self, frame_rate: int) -> None:
        self.tick_rates.append(frame_rate)


@pytest.mark.parametrize(
    ("control_mode", "expected_step_count"),
    [
        (DrivingControlMode.MANUAL, 0),
        (DrivingControlMode.NOA_ACTIVE, 3),
    ],
)
def test_run_schedules_backend_from_canonical_state_once_per_frame(
    monkeypatch,
    control_mode: DrivingControlMode,
    expected_step_count: int,
) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    clock = FakeFrameClock()
    flip_count = 0
    quit_count = 0
    times = iter((0.0, 0.0, 0.1, 0.2, 1.0))

    def record_flip() -> None:
        nonlocal flip_count
        flip_count += 1

    def record_quit() -> None:
        nonlocal quit_count
        quit_count += 1

    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", record_flip)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(driver_view.pygame, "quit", record_quit)

    runtime = FixedAutomationRuntime(
        AutomationState(AutomationAvailability.AVAILABLE, control_mode)
    )
    hero = FakeHero()
    backend = RecordingStepBackend(hero)
    scheduler = AutomationControlScheduler(runtime, backend)
    viewer = driver_view.DriverView(
        FakeWorld(),
        hero,
        DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )
    key_state: defaultdict[int, bool] = defaultdict(bool)
    monkeypatch.setattr(driver_view.pygame.key, "get_pressed", lambda: key_state)
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(viewer, "_draw", lambda screen: None)

    exited_by_user = viewer.run(1.0, scheduler=scheduler)

    assert exited_by_user is False
    assert backend.call_count == expected_step_count
    assert len(hero.control_calls) == 3
    expected_throttle = 0.2 if control_mode is DrivingControlMode.NOA_ACTIVE else 0.0
    assert [control.throttle for control in hero.control_calls] == pytest.approx(
        [expected_throttle] * 3
    )
    assert flip_count == 3
    assert clock.tick_rates == [driver_view.FRAME_RATE] * 3
    assert audio.close_count == 1
    assert quit_count == 1


def test_opt_in_observer_records_each_host_loop_segment(monkeypatch) -> None:
    from src.scenario import driver_view

    class RecordingObserver:
        def __init__(self) -> None:
            self.now = 0.0
            self.segments: list[str] = []

        def timestamp(self) -> float:
            self.now += 0.001
            return self.now

        def record_duration(self, segment: str, duration_seconds: float) -> None:
            assert duration_seconds > 0.0
            self.segments.append(segment)

        def record_camera_preparation(
            self, role, snapshot, prepared_at_seconds
        ) -> None:
            raise AssertionError("draw is replaced in this loop-segment test")

    observer = RecordingObserver()
    clock = FakeFrameClock()
    times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    viewer = driver_view.DriverView(
        FakeWorld(),
        FakeHero(),
        performance_observer=observer,
    )
    monkeypatch.setattr(viewer, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(viewer, "_draw", lambda screen: None)

    viewer.run(1.0)

    assert observer.segments == [
        "scheduler",
        "audio",
        "draw",
        "display_flip",
        "fps_limiter",
        "driver_loop",
    ]
