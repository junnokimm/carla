from __future__ import annotations

import subprocess
import sys
from collections import defaultdict

import pygame
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_driver_view_runtime import FakeFrameClock, FakeTurnSignalAudio


def test_aspect_fit_rect_preserves_logical_view_at_common_display_shapes() -> None:
    from src.scenario.driver_view_presentation import aspect_fit_rect

    assert aspect_fit_rect((1280, 720), (1920, 1080)) == pygame.Rect(0, 0, 1920, 1080)
    assert aspect_fit_rect((1280, 720), (1024, 768)) == pygame.Rect(0, 96, 1024, 576)
    assert aspect_fit_rect((1280, 720), (720, 1280)) == pygame.Rect(0, 437, 720, 405)


def test_f11_uses_borderless_current_monitor_bounds_and_restores_window_geometry(
    monkeypatch,
) -> None:
    from src.scenario import driver_view_presentation
    from src.scenario.driver_view_presentation import DriverViewPresentation

    mode_calls: list[tuple[tuple[int, int], int, int]] = []
    position_calls: list[tuple[int, int]] = []
    window_sizes = iter(((1500, 820),))

    class CurrentWindow:
        display_index = 1

        def __init__(self) -> None:
            self._position = (-1250, 140)

        @property
        def position(self) -> tuple[int, int]:
            return self._position

        @position.setter
        def position(self, value: tuple[int, int]) -> None:
            self._position = value
            position_calls.append(value)

        def set_windowed(self) -> None:
            return None

        @classmethod
        def from_display_module(cls):
            return cls()

    def set_mode(
        size: tuple[int, int], flags: int = 0, *, display: int = 0
    ) -> pygame.Surface:
        mode_calls.append((size, flags, display))
        return pygame.Surface(size)

    monkeypatch.setattr(driver_view_presentation, "Window", CurrentWindow)
    monkeypatch.setattr(
        driver_view_presentation,
        "current_monitor_bounds",
        lambda: (-1920, -240, 1920, 1080),
        raising=False,
    )
    monkeypatch.setattr(pygame.display, "get_window_size", lambda: next(window_sizes))
    monkeypatch.setattr(
        pygame.display,
        "get_desktop_sizes",
        lambda: [(1366, 768), (2560, 1440)],
    )
    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    presentation = DriverViewPresentation((1280, 720), pygame.Surface((1280, 720)))
    press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, repeat=0)
    release = pygame.event.Event(pygame.KEYUP, key=pygame.K_F11)

    presentation.handle_events([press, press])
    presentation.handle_events([release, press])

    assert mode_calls == [
        ((1920, 1080), pygame.NOFRAME, 1),
        ((1500, 820), 0, 1),
    ]
    assert position_calls == [(-1920, -240), (-1250, 140)]


def test_focus_loss_releases_f11_repeat_guard(monkeypatch) -> None:
    from src.scenario import driver_view_presentation
    from src.scenario.driver_view_presentation import DriverViewPresentation

    class CurrentWindow:
        display_index = 0
        position = (100, 80)

        def set_windowed(self) -> None:
            return None

        @classmethod
        def from_display_module(cls):
            return cls()

    mode_calls: list[tuple[int, int]] = []

    def set_mode(
        size: tuple[int, int], flags: int = 0, *, display: int = 0
    ) -> pygame.Surface:
        mode_calls.append(size)
        return pygame.Surface(size)

    monkeypatch.setattr(driver_view_presentation, "Window", CurrentWindow)
    monkeypatch.setattr(
        driver_view_presentation,
        "current_monitor_bounds",
        lambda: (0, 0, 1920, 1080),
        raising=False,
    )
    monkeypatch.setattr(pygame.display, "get_window_size", lambda: (1280, 720))
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [(1920, 1080)])
    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    presentation = DriverViewPresentation((1280, 720), pygame.Surface((1280, 720)))
    press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, repeat=0)

    presentation.handle_events([press])
    presentation.handle_events([pygame.event.Event(pygame.WINDOWFOCUSLOST), press])

    assert mode_calls == [(1920, 1080), (1280, 720)]


@pytest.mark.parametrize("quit_event", [False, True])
def test_run_handles_f11_roundtrips_before_input_observer_without_recreating_sensors(
    monkeypatch,
    quit_event: bool,
) -> None:
    from src.scenario import driver_view, driver_view_presentation

    class CurrentWindow:
        display_index = 0
        position = (100, 80)

        def set_windowed(self) -> None:
            return None

        @classmethod
        def from_display_module(cls):
            return cls()

    class InputObserver:
        def __init__(self) -> None:
            self.inputs = []

        def update(self, driver_input):
            self.inputs.append(driver_input)
            return AutomationState(
                AutomationAvailability.AVAILABLE,
                DrivingControlMode.MANUAL,
            )

    audio = FakeTurnSignalAudio()
    clock = FakeFrameClock()
    mode_calls: list[tuple[tuple[int, int], int, int]] = []
    window = pygame.Surface((1280, 720))
    fullscreen = pygame.Surface((1920, 1080))

    def set_mode(
        size: tuple[int, int], flags: int = 0, *, display: int = 0
    ) -> pygame.Surface:
        mode_calls.append((size, flags, display))
        return fullscreen if flags == pygame.NOFRAME else window

    f11 = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, repeat=0)
    noa = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)
    release = pygame.event.Event(pygame.KEYUP, key=pygame.K_F11)
    exit_event = (
        pygame.event.Event(pygame.QUIT)
        if quit_event
        else pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, repeat=0)
    )
    event_batches = iter(
        (
            [f11, f11, noa],
            [release, f11],
            [release, f11],
            [release, f11, release],
            [exit_event],
        )
    )
    times = iter((0.0, 0.0, 0.1, 0.2, 0.3, 0.4))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", set_mode)
    monkeypatch.setattr(
        driver_view.pygame.display, "get_window_size", lambda: (1280, 720)
    )
    monkeypatch.setattr(
        driver_view.pygame.display, "get_desktop_sizes", lambda: [(1920, 1080)]
    )
    monkeypatch.setattr(driver_view_presentation, "Window", CurrentWindow)
    monkeypatch.setattr(
        driver_view_presentation,
        "current_monitor_bounds",
        lambda: (0, 0, 1920, 1080),
        raising=False,
    )
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", lambda: next(event_batches))
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    monkeypatch.setattr(
        driver_view.pygame.key,
        "get_pressed",
        lambda: defaultdict(bool),
    )
    world = FakeWorld()
    viewer = driver_view.DriverView(world, FakeHero())
    viewer.attach()
    sensors = viewer.sensors
    observer = InputObserver()
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)

    exited = viewer.run(10.0, input_observer=observer)

    assert exited is True
    assert mode_calls == [
        ((1280, 720), 0, 0),
        ((1920, 1080), pygame.NOFRAME, 0),
        ((1280, 720), 0, 0),
        ((1920, 1080), pygame.NOFRAME, 0),
        ((1280, 720), 0, 0),
    ]
    assert len(observer.inputs) == 4
    assert observer.inputs[0].activation_requested is True
    assert viewer.sensors == sensors
    assert len(world.spawn_calls) == 3
    assert all(sensor.stop_count == 0 for sensor in sensors)
    assert all(sensor.destroy_count == 0 for sensor in sensors)


def test_real_dummy_sdl_restores_window_and_surface_size(monkeypatch) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """
import pygame
from src.scenario import driver_view_presentation
from src.scenario.driver_view_presentation import DriverViewPresentation
pygame.display.init()
try:
    size = (1280, 720)
    desktop = pygame.display.get_desktop_sizes()[0]
    driver_view_presentation.current_monitor_bounds = lambda: (0, 0, *desktop)
    display = DriverViewPresentation(size, pygame.display.set_mode(size))
    display.canvas.fill((30, 60, 90))
    press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)
    release = pygame.event.Event(pygame.KEYUP, key=pygame.K_F11)
    for _ in range(2):
        display.handle_events([press])
        display.present()
        assert pygame.display.get_window_size() == desktop
        display.handle_events([release, press, release])
        display.present()
        assert pygame.display.get_window_size() == size
        assert pygame.display.get_surface().get_size() == size
        assert pygame.display.get_surface().get_at((1279, 719))[:3] == (30, 60, 90)
finally:
    pygame.quit()
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
