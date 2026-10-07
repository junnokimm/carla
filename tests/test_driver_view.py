from __future__ import annotations

from dataclasses import dataclass

import carla
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
        self.attributes: dict[str, str] = {}
        self.stop_count = 0
        self.destroy_count = 0
        self.transforms: list[carla.Transform] = []

    def listen(self, callback) -> None:
        self.callback = callback

    def stop(self) -> None:
        self.stop_count += 1

    def destroy(self) -> bool:
        self.destroy_count += 1
        return True

    def set_transform(self, transform: carla.Transform) -> None:
        self.transforms.append(transform)


class FakeHero:
    def __init__(self) -> None:
        self.autopilot_calls: list[bool] = []
        self.control_calls: list[carla.VehicleControl] = []
        self.operations: list[tuple[str, bool | carla.VehicleControl]] = []
        self.velocity = carla.Vector3D()
        self.current_control = carla.VehicleControl()

    def set_autopilot(self, enabled: bool) -> None:
        self.autopilot_calls.append(enabled)
        self.operations.append(("autopilot", enabled))

    def apply_control(self, control: carla.VehicleControl) -> None:
        self.current_control = control
        self.control_calls.append(control)
        self.operations.append(("control", control))

    def get_velocity(self) -> carla.Vector3D:
        return self.velocity

    def get_control(self) -> carla.VehicleControl:
        return self.current_control


@dataclass
class SpawnCall:
    blueprint: FakeBlueprint
    transform: object
    attached_to: object
    attributes: dict[str, str]


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
        sensor.attributes = blueprint.attributes.copy()
        self.spawn_calls.append(
            SpawnCall(blueprint, transform, attach_to, blueprint.attributes.copy())
        )
        self.sensors.append(sensor)
        return sensor


def test_attach_creates_four_distinguishable_rgb_cameras_on_hero() -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeHero()
    world = FakeWorld()
    viewer = DriverView(world, hero)

    viewer.attach()

    assert [feed.role for feed in viewer.feeds] == ["front", "rear", "left", "right"]
    assert len(world.spawn_calls) == 4
    assert all(
        call.blueprint.identifier == "sensor.camera.rgb" for call in world.spawn_calls
    )
    assert all(call.attached_to is hero for call in world.spawn_calls)
    assert len(viewer.sensors) == 4
    rear = world.spawn_calls[1]
    assert rear.attributes["image_size_x"] == "640"
    assert rear.attributes["image_size_y"] == "180"
    assert rear.attributes["fov"] == "90.0"
    assert rear.transform.location.x == pytest.approx(-2.5)
    assert rear.transform.location.y == pytest.approx(0.0)
    assert rear.transform.location.z == pytest.approx(1.3)
    assert rear.transform.rotation.yaw == pytest.approx(180.0)
    assert rear.transform.rotation.yaw not in {
        world.spawn_calls[2].transform.rotation.yaw,
        world.spawn_calls[3].transform.rotation.yaw,
    }


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


def test_close_cleans_all_existing_feeds_when_fourth_camera_spawn_fails() -> None:
    from src.scenario.driver_view import DriverView

    world = FakeWorld(fail_at_camera=4)
    viewer = DriverView(world, FakeHero())

    with pytest.raises(RuntimeError, match="camera spawn failed"):
        viewer.attach()
    viewer.close()

    assert [(sensor.stop_count, sensor.destroy_count) for sensor in world.sensors] == [
        (1, 1),
        (1, 1),
        (1, 1),
    ]
    assert viewer.sensors == ()


def test_protected_front_cockpit_and_hud_configuration_is_unchanged() -> None:
    from src.scenario.driver_view import CameraTransform, DriverViewConfig

    config = DriverViewConfig()

    assert config.front_resolution == (1280, 720)
    assert config.fov == 100.0
    assert config.front_transform == CameraTransform(x=1.4, y=0.0, z=1.3, pitch=-2.0)
    assert config.cockpit_fov == 105.0
    assert config.cockpit_transform == CameraTransform(
        x=0.10, y=-0.35, z=1.20, pitch=-1.5
    )
    assert config.hud_anchor_ratio == (0.62, 0.62)
    assert config.hud_toast_y_ratio == 0.58


def test_camera_feed_snapshot_keeps_latest_frame_metadata_on_one_host_clock() -> None:
    from src.scenario.driver_view import CameraFeed

    class FakeImage:
        frame = 42
        timestamp = 12.5

    receipt_times = iter((100.25,))
    image = FakeImage()
    feed = CameraFeed("front", FakeSensor())
    feed.receipt_clock = receipt_times.__next__

    feed.receive(image)
    snapshot = feed.snapshot()

    assert snapshot.image is image
    assert snapshot.generation == 1
    assert snapshot.callback_count == 1
    assert snapshot.first_sensor_frame == 42
    assert snapshot.last_sensor_frame == 42
    assert snapshot.first_sensor_timestamp_seconds == 12.5
    assert snapshot.last_sensor_timestamp_seconds == 12.5
    assert snapshot.first_host_receipt_seconds == 100.25
    assert snapshot.last_host_receipt_seconds == 100.25


def test_layout_places_rear_above_road_and_sides_in_lower_dashboard_gaps() -> None:
    from src.scenario.driver_view import DriverViewConfig, calculate_layout

    config = DriverViewConfig(window_size=(1280, 720))

    layout = calculate_layout(config)

    assert layout.front_position == (0, 0)
    assert layout.rear_mirror.bounds == (480, 100, 320, 90)
    assert layout.left_mirror.bounds == (24, 561, 240, 135)
    assert layout.right_mirror.bounds == (1016, 561, 240, 135)
    assert config.mirror_size == (240, 135)
    assert layout.left_mirror_position[1] == layout.right_mirror_position[1]


def test_layout_recomputes_from_custom_window_and_legacy_side_size() -> None:
    from src.scenario.driver_view import DriverViewConfig, calculate_layout

    config = DriverViewConfig(
        window_size=(1600, 900),
        mirror_size=(300, 168),
        mirror_margin=32,
    )

    layout = calculate_layout(config)

    assert layout.rear_mirror.bounds == (640, 125, 320, 90)
    assert layout.left_mirror.bounds == (32, 700, 300, 168)
    assert layout.right_mirror.bounds == (1268, 700, 300, 168)


def test_default_mirror_rectangles_avoid_protected_regions_and_each_other() -> None:
    from src.scenario.driver_view import DriverViewConfig, calculate_layout
    from src.scenario.mirror_layout import ScreenRect

    layout = calculate_layout(DriverViewConfig())
    mirrors = (layout.rear_mirror, layout.left_mirror, layout.right_mirror)
    protected = (
        ScreenRect(x=480, y=28, width=320, height=57),
        ScreenRect(x=0, y=200, width=1280, height=250),
        ScreenRect(x=500, y=500, width=320, height=150),
    )

    assert all(
        not first.intersects(second)
        for index, first in enumerate(mirrors)
        for second in mirrors[index + 1 :]
    )
    assert all(
        not mirror.intersects(region) for mirror in mirrors for region in protected
    )


def test_p_keydown_toggles_driving_mode_once() -> None:
    from src.scenario.driver_view import DriverView
    from src.vehicle.driving_mode import DrivingMode

    hero = FakeHero()
    viewer = DriverView(FakeWorld(), hero)
    event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)

    viewer._handle_mode_events([event])

    assert viewer.driving_mode is DrivingMode.MANUAL
    assert hero.autopilot_calls == [True, False]


def test_h_keydown_toggles_hud_without_changing_vehicle_state() -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeHero()
    world = FakeWorld()
    viewer = DriverView(world, hero)
    viewer.attach()
    sensors = viewer.sensors
    autopilot_calls = tuple(hero.autopilot_calls)
    control_calls = tuple(hero.control_calls)
    toggle = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h)

    viewer._handle_mode_events([toggle])

    assert viewer.hud_enabled is False
    assert viewer.sensors == sensors
    assert tuple(hero.autopilot_calls) == autopilot_calls
    assert tuple(hero.control_calls) == control_calls

    viewer._handle_mode_events([toggle])

    assert viewer.hud_enabled is True


def test_manual_mode_applies_held_keys_each_frame_with_smooth_steering(
    monkeypatch,
) -> None:
    from src.scenario.driver_view import DriverView

    held_keys = {pygame.K_w, pygame.K_a, pygame.K_SPACE}

    class HeldKeys:
        def __getitem__(self, key: int) -> bool:
            return key in held_keys

    hero = FakeHero()
    viewer = DriverView(FakeWorld(), hero)
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    monkeypatch.setattr(pygame.key, "get_pressed", HeldKeys)

    viewer._apply_manual_control()
    viewer._apply_manual_control()

    first, second = hero.control_calls[-2:]
    assert first.throttle == pytest.approx(1.0)
    assert first.brake == pytest.approx(0.0)
    assert first.hand_brake is True
    assert -1.0 < second.steer < first.steer < 0.0

    held_keys.clear()
    held_keys.update({pygame.K_s, pygame.K_d})
    viewer._apply_manual_control()
    viewer._apply_manual_control()
    viewer._apply_manual_control()

    braking_right = hero.control_calls[-1]
    assert braking_right.throttle == pytest.approx(0.0)
    assert braking_right.brake == pytest.approx(1.0)
    assert braking_right.hand_brake is False
    assert 0.0 < braking_right.steer < 1.0


def test_manual_to_autopilot_neutralizes_control_before_enabling_autopilot(
    monkeypatch,
) -> None:
    from src.scenario.driver_view import DriverView

    class HeldThrottle:
        def __getitem__(self, key: int) -> bool:
            return key == pygame.K_w

    hero = FakeHero()
    viewer = DriverView(FakeWorld(), hero)
    toggle = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
    viewer._handle_mode_events([toggle])
    monkeypatch.setattr(pygame.key, "get_pressed", HeldThrottle)
    viewer._apply_manual_control()

    viewer._handle_mode_events([toggle])
    calls_after_toggle = len(hero.control_calls)
    viewer._apply_manual_control()

    neutral_operation, autopilot_operation = hero.operations[-2:]
    assert neutral_operation[0] == "control"
    neutral_control = neutral_operation[1]
    assert isinstance(neutral_control, carla.VehicleControl)
    assert neutral_control.throttle == pytest.approx(0.0)
    assert neutral_control.brake == pytest.approx(0.0)
    assert neutral_control.steer == pytest.approx(0.0)
    assert autopilot_operation == ("autopilot", True)
    assert len(hero.control_calls) == calls_after_toggle


def test_draw_keeps_front_raw_and_flips_all_three_mirrors(monkeypatch) -> None:
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

    surfaces = iter(("front", "rear", "left", "right"))
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
        CameraFeed("rear", FakeSensor(), FakeImage()),
        CameraFeed("left", FakeSensor(), FakeImage()),
        CameraFeed("right", FakeSensor(), FakeImage()),
    ]
    screen = FakeScreen()

    viewer._draw(screen)

    assert flip_calls == [
        (("scaled", "rear", (320, 90)), True, False),
        (("scaled", "left", (240, 135)), True, False),
        (("scaled", "right", (240, 135)), True, False),
    ]
    assert screen.blits == [
        (("scaled", "front", (1280, 720)), (0, 0)),
        (("flipped", "rear", (320, 90)), (480, 100)),
        (("flipped", "left", (240, 135)), (24, 561)),
        (("flipped", "right", (240, 135)), (1016, 561)),
    ]
