from __future__ import annotations

import carla
import pygame
import pytest

from src.scenario.driver_hud import Gear
from src.vehicle.driving_mode import DrivingMode
from tests.test_driver_view import FakeHero, FakeSensor, FakeWorld


class RecordingHudRenderer:
    def __init__(self) -> None:
        self.states = []

    def draw(self, screen, state) -> None:
        self.states.append(state)


def prepare_viewer(hero: FakeHero | None = None):
    from src.scenario.driver_view import CameraFeed, DriverView

    viewer = DriverView(FakeWorld(), hero or FakeHero())
    viewer._feeds = [
        CameraFeed("front", FakeSensor()),
        CameraFeed("left", FakeSensor()),
        CameraFeed("right", FakeSensor()),
    ]
    renderer = RecordingHudRenderer()
    viewer._hud_renderer = renderer
    return viewer, renderer


@pytest.mark.parametrize(
    ("hud_enabled", "cockpit_view", "expected_draws"),
    [
        (True, True, 1),
        (False, True, 0),
        (True, False, 0),
    ],
)
def test_hud_visibility_requires_enabled_cockpit_view(
    hud_enabled: bool, cockpit_view: bool, expected_draws: int
) -> None:
    viewer, renderer = prepare_viewer()
    viewer._hud_enabled = hud_enabled
    viewer._cockpit_view = cockpit_view

    viewer._draw(pygame.Surface((1280, 720)))

    assert len(renderer.states) == expected_draws


def test_hud_state_uses_live_speed_gear_and_driving_mode() -> None:
    hero = FakeHero()
    hero.velocity = carla.Vector3D(x=3.0, y=4.0, z=0.0)
    hero.current_control = carla.VehicleControl(gear=3)
    viewer, renderer = prepare_viewer(hero)
    viewer._cockpit_view = True

    viewer._draw(pygame.Surface((1280, 720)))

    state = renderer.states[-1]
    assert state.speed_kmh == pytest.approx(18.0)
    assert state.gear is Gear.DRIVE
    assert state.driving_mode is DrivingMode.AUTONOMOUS


def test_hud_mode_follows_p_key_source_of_truth() -> None:
    viewer, renderer = prepare_viewer()
    viewer._cockpit_view = True

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    viewer._draw(pygame.Surface((1280, 720)))

    assert renderer.states[-1].driving_mode is DrivingMode.MANUAL


def test_mode_toast_follows_both_mode_changes_and_expires(monkeypatch) -> None:
    from src.scenario import driver_view

    now = 100.0
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: now)
    viewer, renderer = prepare_viewer()
    viewer._cockpit_view = True
    screen = pygame.Surface((1280, 720))

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    viewer._draw(screen)

    assert renderer.states[-1].driving_mode is DrivingMode.MANUAL
    assert renderer.states[-1].mode_toast_alpha == 255

    now += 2.0
    viewer._draw(screen)

    assert renderer.states[-1].mode_toast_alpha is None

    now += 1.0
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    viewer._draw(screen)

    assert renderer.states[-1].driving_mode is DrivingMode.AUTONOMOUS
    assert renderer.states[-1].mode_toast_alpha == 255


def test_hud_off_discards_mode_toasts(monkeypatch) -> None:
    from src.scenario import driver_view

    now = 100.0
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: now)
    viewer, renderer = prepare_viewer()
    viewer._cockpit_view = True
    screen = pygame.Surface((1280, 720))

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    viewer._draw(screen)
    assert renderer.states[-1].mode_toast_alpha == 255
    renderer.states.clear()

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h)])
    viewer._draw(screen)
    assert renderer.states == []

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    viewer._draw(screen)

    assert renderer.states == []

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h)])
    viewer._draw(screen)

    assert renderer.states[-1].mode_toast_alpha is None


def test_hud_enabled_state_survives_v_key_round_trip() -> None:
    viewer, renderer = prepare_viewer()
    screen = pygame.Surface((1280, 720))

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h)])
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v)])
    viewer._draw(screen)
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v)])
    viewer._draw(screen)

    assert viewer.hud_enabled is False
    assert renderer.states == []


def test_hud_enabled_state_survives_driver_cockpit_round_trip() -> None:
    viewer, renderer = prepare_viewer()
    viewer._cockpit_view = True
    screen = pygame.Surface((1280, 720))

    viewer._draw(screen)
    viewer._cockpit_view = False
    viewer._draw(screen)
    viewer._cockpit_view = True
    viewer._draw(screen)

    assert len(renderer.states) == 2


def test_hud_is_drawn_between_front_view_and_mirrors(monkeypatch) -> None:
    from src.scenario import driver_view
    from src.scenario.driver_view import CameraFeed, DriverView

    class FakeImage:
        raw_data = bytes(4)
        width = 1
        height = 1

    class RecordingScreen:
        def __init__(self) -> None:
            self.layers: list[str] = []

        def blit(self, surface, position) -> None:
            self.layers.append(surface)

    class LayerRecordingHud:
        def draw(self, screen, state) -> None:
            screen.blit("hud", (0, 0))

    images = iter(("front", "left", "right"))
    monkeypatch.setattr(
        driver_view.pygame.image,
        "frombuffer",
        lambda raw_data, size, format_name: next(images),
    )
    monkeypatch.setattr(
        driver_view.pygame.transform, "smoothscale", lambda surface, size: surface
    )
    monkeypatch.setattr(
        driver_view.pygame.transform,
        "flip",
        lambda surface, flip_x, flip_y: surface,
    )
    viewer = DriverView(FakeWorld(), FakeHero())
    viewer._feeds = [
        CameraFeed("front", FakeSensor(), FakeImage()),
        CameraFeed("left", FakeSensor(), FakeImage()),
        CameraFeed("right", FakeSensor(), FakeImage()),
    ]
    viewer._cockpit_view = True
    viewer._hud_renderer = LayerRecordingHud()
    screen = RecordingScreen()

    viewer._draw(screen)

    assert screen.layers == ["front", "hud", "left", "right"]
