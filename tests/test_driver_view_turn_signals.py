from __future__ import annotations

import carla
import pygame
import pytest

from tests.test_driver_view import FakeHero, FakeWorld


class FakeLightHero(FakeHero):
    def __init__(self) -> None:
        super().__init__()
        self.light_state = carla.VehicleLightState.NONE
        self.light_state_calls: list[carla.VehicleLightState] = []

    def get_light_state(self) -> carla.VehicleLightState:
        return self.light_state

    def set_light_state(self, light_state: carla.VehicleLightState) -> None:
        self.light_state = light_state
        self.light_state_calls.append(light_state)


class FakeTurnSignalAudio:
    def __init__(self) -> None:
        self.updates: list[tuple[bool, float]] = []
        self.close_count = 0

    def update(self, *, active: bool, now: float) -> None:
        self.updates.append((active, now))

    def close(self) -> None:
        self.close_count += 1


@pytest.mark.parametrize(
    ("initial_state", "key", "expected_state"),
    [
        (carla.VehicleLightState.NONE, pygame.K_z, carla.VehicleLightState.LeftBlinker),
        (carla.VehicleLightState.LeftBlinker, pygame.K_z, carla.VehicleLightState.NONE),
        (carla.VehicleLightState.NONE, pygame.K_x, carla.VehicleLightState.RightBlinker),
        (carla.VehicleLightState.RightBlinker, pygame.K_x, carla.VehicleLightState.NONE),
        (
            carla.VehicleLightState.LeftBlinker,
            pygame.K_x,
            carla.VehicleLightState.RightBlinker,
        ),
        (
            carla.VehicleLightState.RightBlinker,
            pygame.K_z,
            carla.VehicleLightState.LeftBlinker,
        ),
    ],
)
def test_manual_turn_signal_key_toggles_exclusive_vehicle_light_state(
    initial_state: carla.VehicleLightState,
    key: int,
    expected_state: carla.VehicleLightState,
) -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeLightHero()
    viewer = DriverView(FakeWorld(), hero)
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    hero.light_state = initial_state

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=key)])

    assert hero.light_state == expected_state
    assert hero.light_state_calls == [expected_state]


def test_turn_signal_toggle_preserves_unrelated_vehicle_light_flags() -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeLightHero()
    viewer = DriverView(FakeWorld(), hero)
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    hero.light_state = carla.VehicleLightState(
        carla.VehicleLightState.LowBeam | carla.VehicleLightState.LeftBlinker
    )

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x)])

    assert hero.light_state == (
        carla.VehicleLightState.LowBeam | carla.VehicleLightState.RightBlinker
    )


@pytest.mark.parametrize("key", [pygame.K_z, pygame.K_x])
def test_autonomous_mode_ignores_turn_signal_keys(key: int) -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeLightHero()
    hero.light_state = carla.VehicleLightState.LowBeam
    viewer = DriverView(FakeWorld(), hero)

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=key)])

    assert hero.light_state == carla.VehicleLightState.LowBeam
    assert hero.light_state_calls == []


def test_turn_signal_audio_follows_actual_vehicle_lights_and_closes(
    monkeypatch,
) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    times = iter((1.0, 2.0, 3.0, 4.0))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    hero = FakeLightHero()
    viewer = driver_view.DriverView(FakeWorld(), hero)

    viewer._update_turn_signal_audio()
    hero.light_state = carla.VehicleLightState.LeftBlinker
    viewer._update_turn_signal_audio()
    hero.light_state = carla.VehicleLightState.RightBlinker
    viewer._update_turn_signal_audio()
    hero.light_state = carla.VehicleLightState.NONE
    viewer._update_turn_signal_audio()
    viewer.close()

    assert audio.updates == [
        (False, 1.0),
        (True, 2.0),
        (True, 3.0),
        (False, 4.0),
    ]
    assert audio.close_count == 1


def test_escape_exit_closes_turn_signal_audio_before_pygame_shutdown(
    monkeypatch,
) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    operations: list[str] = []
    times = iter((0.0, 0.1))

    def reject_eager_init() -> None:
        raise AssertionError("mixer must initialize lazily")

    monkeypatch.setattr(audio, "close", lambda: operations.append("audio.close"))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame, "init", reject_eager_init)
    monkeypatch.setattr(
        driver_view.pygame.display,
        "init",
        lambda: operations.append("display.init"),
    )
    monkeypatch.setattr(
        driver_view.pygame,
        "quit",
        lambda: operations.append("pygame.quit"),
    )
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(
        driver_view.pygame.event,
        "get",
        lambda: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],
    )
    viewer = driver_view.DriverView(FakeWorld(), FakeLightHero())

    exited_by_user = viewer.run(1.0)

    assert exited_by_user is True
    assert operations == ["display.init", "audio.close", "pygame.quit"]
