from __future__ import annotations

from dataclasses import dataclass

import pygame
import pytest


class FakeBlueprint:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        self.attributes: dict[str, str] = {}

    def set_attribute(self, name: str, value: str) -> None:
        self.attributes[name] = value


class FakeBlueprintLibrary:
    def __init__(self) -> None:
        self.camera_blueprint = FakeBlueprint("sensor.camera.rgb")

    def find(self, identifier: str) -> FakeBlueprint:
        assert identifier == "sensor.camera.rgb"
        return self.camera_blueprint


class FakeSensor:
    def __init__(self) -> None:
        self.callback = None
        self.stop_count = 0
        self.destroy_count = 0

    def listen(self, callback) -> None:
        self.callback = callback

    def stop(self) -> None:
        self.stop_count += 1

    def destroy(self) -> bool:
        self.destroy_count += 1
        return True


class FakeHero:
    def __init__(self) -> None:
        self.autopilot_calls: list[bool] = []

    def set_autopilot(self, enabled: bool) -> None:
        self.autopilot_calls.append(enabled)


@dataclass
class SpawnCall:
    blueprint: FakeBlueprint
    transform: object
    attached_to: object


class FakeWorld:
    def __init__(self, fail_at_camera: int | None = None) -> None:
        self.blueprints = FakeBlueprintLibrary()
        self.fail_at_camera = fail_at_camera
        self.spawn_calls: list[SpawnCall] = []
        self.sensors: list[FakeSensor] = []

    def get_blueprint_library(self) -> FakeBlueprintLibrary:
        return self.blueprints

    def spawn_actor(self, blueprint, transform, attach_to) -> FakeSensor:
        if self.fail_at_camera == len(self.spawn_calls) + 1:
            raise RuntimeError("camera spawn failed")
        sensor = FakeSensor()
        self.spawn_calls.append(SpawnCall(blueprint, transform, attach_to))
        self.sensors.append(sensor)
        return sensor


def test_attach_creates_three_distinguishable_rgb_cameras_on_hero() -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeHero()
    world = FakeWorld()
    viewer = DriverView(world, hero)

    viewer.attach()

    assert [feed.role for feed in viewer.feeds] == ["front", "left", "right"]
    assert len(world.spawn_calls) == 3
    assert all(
        call.blueprint.identifier == "sensor.camera.rgb" for call in world.spawn_calls
    )
    assert all(call.attached_to is hero for call in world.spawn_calls)
    assert len(viewer.sensors) == 3


def test_close_stops_and_destroys_every_tracked_camera() -> None:
    from src.scenario.driver_view import DriverView

    world = FakeWorld()
    viewer = DriverView(world, FakeHero())
    viewer.attach()

    viewer.close()

    assert [(sensor.stop_count, sensor.destroy_count) for sensor in world.sensors] == [
        (1, 1),
        (1, 1),
        (1, 1),
    ]
    assert viewer.sensors == ()


def test_close_is_safe_when_camera_initialization_is_incomplete() -> None:
    from src.scenario.driver_view import DriverView

    world = FakeWorld(fail_at_camera=2)
    viewer = DriverView(world, FakeHero())

    with pytest.raises(RuntimeError, match="camera spawn failed"):
        viewer.attach()
    viewer.close()

    assert [(sensor.stop_count, sensor.destroy_count) for sensor in world.sensors] == [
        (1, 1)
    ]
    assert viewer.sensors == ()


def test_layout_places_mirrors_in_opposite_upper_regions() -> None:
    from src.scenario.driver_view import DriverViewConfig, calculate_layout

    config = DriverViewConfig(window_size=(1280, 720), mirror_size=(320, 180))

    layout = calculate_layout(config)

    assert layout.front_position == (0, 0)
    assert layout.left_mirror_position == (24, 24)
    assert layout.right_mirror_position == (936, 24)
    assert config.mirror_size == (320, 180)
    assert layout.left_mirror_position[1] == layout.right_mirror_position[1]


def test_p_keydown_toggles_driving_mode_once() -> None:
    from src.scenario.driver_view import DriverView
    from src.vehicle.driving_mode import DrivingMode

    hero = FakeHero()
    viewer = DriverView(FakeWorld(), hero)
    event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)

    viewer._handle_mode_events([event])

    assert viewer.driving_mode is DrivingMode.MANUAL
    assert hero.autopilot_calls == [True, False]


def test_draw_keeps_front_raw_and_flips_both_mirrors(monkeypatch) -> None:
    from src.scenario import driver_view
    from src.scenario.driver_view import CameraFeed, DriverView

    class FakeImage:
        raw_data = bytes(4)
        width = 1
        height = 1

    class FakeScreen:
        def __init__(self) -> None:
            self.blits: list[
                tuple[tuple[str, str, tuple[int, int]], tuple[int, int]]
            ] = []

        def blit(
            self,
            surface: tuple[str, str, tuple[int, int]],
            position: tuple[int, int],
        ) -> None:
            self.blits.append((surface, position))

    surfaces = iter(("front", "left", "right"))
    flip_calls: list[tuple[tuple[str, str, tuple[int, int]], bool, bool]] = []
    monkeypatch.setattr(
        driver_view.pygame.image,
        "frombuffer",
        lambda raw_data, size, format_name: next(surfaces),
    )
    monkeypatch.setattr(
        driver_view.pygame.transform,
        "smoothscale",
        lambda surface, size: ("scaled", surface, size),
    )
    monkeypatch.setattr(
        driver_view.pygame.transform,
        "flip",
        lambda surface, flip_x, flip_y: (
            flip_calls.append((surface, flip_x, flip_y))
            or ("flipped", surface[1], surface[2])
        ),
    )
    viewer = DriverView(FakeWorld(), FakeHero())
    viewer._feeds = [
        CameraFeed("front", FakeSensor(), FakeImage()),
        CameraFeed("left", FakeSensor(), FakeImage()),
        CameraFeed("right", FakeSensor(), FakeImage()),
    ]
    screen = FakeScreen()

    viewer._draw(screen)

    assert flip_calls == [
        (("scaled", "left", (320, 180)), True, False),
        (("scaled", "right", (320, 180)), True, False),
    ]
    assert screen.blits == [
        (("scaled", "front", (1280, 720)), (0, 0)),
        (("flipped", "left", (320, 180)), (24, 24)),
        (("flipped", "right", (320, 180)), (936, 24)),
    ]
