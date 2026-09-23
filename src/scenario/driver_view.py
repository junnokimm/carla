from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import carla
import pygame

from src.scenario.driver_hud import (
    DriverHudRenderer,
    HudRenderer,
    HudState,
    calculate_speed_kmh,
    gear_from_control,
)
from src.vehicle.driving_mode import DrivingMode, DrivingModeController

CAMERA_BLUEPRINT_ID: Final = "sensor.camera.rgb"
CAMERA_FOV: Final = 100.0
FRAME_RATE: Final = 60
STEER_INCREMENT: Final = 0.04


@dataclass(frozen=True, slots=True)
class CameraTransform:
    """Vehicle-relative camera pose expressed in CARLA coordinates."""

    x: float
    y: float
    z: float
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0

    def to_carla(self) -> carla.Transform:
        """Build the CARLA transform used when attaching a camera."""
        return carla.Transform(
            carla.Location(x=self.x, y=self.y, z=self.z),
            carla.Rotation(pitch=self.pitch, yaw=self.yaw, roll=self.roll),
        )

@dataclass(frozen=True, slots=True)
class DriverViewConfig:
    """Small set of easily adjustable driver-view prototype settings."""

    window_size: tuple[int, int] = (1280, 720)
    front_resolution: tuple[int, int] = (1280, 720)
    mirror_resolution: tuple[int, int] = (480, 270)
    mirror_size: tuple[int, int] = (320, 180)
    mirror_margin: int = 24
    fov: float = CAMERA_FOV
    cockpit_fov: float = 105.0
    mirror_fov: float = 100.0
    hud_anchor_ratio: tuple[float, float] = (0.62, 0.62)
    hud_toast_y_ratio: float = 0.58
    hud_mode_toast_duration: float = 1.75
    initial_driving_mode: DrivingMode = DrivingMode.AUTONOMOUS
    front_transform: CameraTransform = CameraTransform(x=1.4, y=0.0, z=1.3, pitch=-2.0)

# 콕핏 뷰 카메라 위치 조정
    # cockpit_transform: CameraTransform = CameraTransform(x=0.2, y=-0.35, z=1.30, pitch=-1.5) 
    cockpit_transform: CameraTransform = CameraTransform(x=0.10, y=-0.35, z=1.20, pitch=-1.5) 
    left_mirror_transform: CameraTransform = CameraTransform(
        x=0.3, y=-1.0, z=1.2, yaw=-150.0
    )
    right_mirror_transform: CameraTransform = CameraTransform(
        x=0.3, y=1.0, z=1.2, yaw=150.0
    )


@dataclass(frozen=True, slots=True)
class DriverViewLayout:
    """Screen positions for the composed driver-view feeds."""

    front_position: tuple[int, int]
    left_mirror_position: tuple[int, int]
    right_mirror_position: tuple[int, int]


class DriverViewCleanupError(RuntimeError):
    """Raised after every camera cleanup operation was attempted but one failed."""

    def __init__(self, errors: tuple[RuntimeError, ...]) -> None:
        self.errors = errors
        super().__init__("Unable to clean up all driver-view cameras.")


def calculate_layout(config: DriverViewConfig) -> DriverViewLayout:
    """Place mirror overlays at the upper corners of the front view."""
    window_width, _ = config.window_size
    mirror_width, _ = config.mirror_size
    return DriverViewLayout(
        front_position=(0, 0),
        left_mirror_position=(config.mirror_margin, config.mirror_margin),
        right_mirror_position=(
            window_width - mirror_width - config.mirror_margin,
            config.mirror_margin,
        ),
    )


@dataclass(slots=True)
class CameraFeed:
    """Own a mutable latest image because CARLA sensor callbacks arrive asynchronously."""

    role: str
    sensor: carla.Actor
    latest_image: carla.Image | None = None

    def receive(self, image: carla.Image) -> None:
        """Keep only the most recently delivered camera frame."""
        self.latest_image = image


class DriverView:
    """Attach and compose a forward feed with two rear-facing mirror feeds."""

    def __init__(
        self,
        world: carla.World,
        hero: carla.Actor,
        config: DriverViewConfig | None = None,
    ) -> None:
        self._world = world
        self._hero = hero
        self._config = config or DriverViewConfig()
        self._driving_mode_controller = DrivingModeController(
            hero, self._config.initial_driving_mode
        )
        self._steer = 0.0
        self._cockpit_view = False
        self._hud_enabled = True
        self._mode_toast_started_at: float | None = None
        self._hud_renderer: HudRenderer = DriverHudRenderer(
            self._config.window_size,
            anchor_ratio=self._config.hud_anchor_ratio,
            toast_y_ratio=self._config.hud_toast_y_ratio,
        )
        self._feeds: list[CameraFeed] = []

    @property
    def feeds(self) -> tuple[CameraFeed, ...]:
        """Return each camera feed in composition order."""
        return tuple(self._feeds)

    @property
    def sensors(self) -> tuple[carla.Actor, ...]:
        """Return all camera actors currently owned by this view."""
        return tuple(feed.sensor for feed in self._feeds)

    @property
    def driving_mode(self) -> DrivingMode:
        """Return the current hero driving mode."""
        return self._driving_mode_controller.mode

    @property
    def hud_enabled(self) -> bool:
        """Return whether the cockpit HUD is enabled."""
        return self._hud_enabled

    def attach(self) -> None:
        """Create and attach the front, left-mirror, and right-mirror RGB cameras."""
        mounts: Sequence[tuple[str, CameraTransform, tuple[int, int], float]] = (
            ("front", self._config.front_transform, self._config.front_resolution, self._config.fov),
            ("left", self._config.left_mirror_transform, self._config.mirror_resolution, self._config.mirror_fov),
            ("right", self._config.right_mirror_transform, self._config.mirror_resolution, self._config.mirror_fov),
        )
        blueprint = self._world.get_blueprint_library().find(CAMERA_BLUEPRINT_ID)
        for role, transform, resolution, fov in mounts:
            width, height = resolution
            blueprint.set_attribute("image_size_x", str(width))
            blueprint.set_attribute("image_size_y", str(height))
            blueprint.set_attribute("fov", str(fov))

            if role == "front": # 새로 추가한 부분, front 카메라의 exposure를 살짝 올려보기
                blueprint.set_attribute("exposure_compensation", "0.5")
            else:
                blueprint.set_attribute("exposure_compensation", "0.0")

            sensor = self._world.spawn_actor(
                blueprint,
                transform.to_carla(),
                attach_to=self._hero,
            )
            feed = CameraFeed(role=role, sensor=sensor)
            self._feeds.append(feed)
            sensor.listen(feed.receive)

    def run(self, duration: float) -> bool:
        """Show the composed view until duration elapses or the user exits it."""
        pygame.init()
        try:
            screen = pygame.display.set_mode(self._config.window_size)
            pygame.display.set_caption("CARLA driver view")
            clock = pygame.time.Clock()
            started_at = time.monotonic()
            exited_by_user = False
            while time.monotonic() - started_at < duration:
                events = pygame.event.get()
                self._handle_mode_events(events)
                if self._exit_requested(events):
                    exited_by_user = True
                    break
                self._apply_manual_control()
                self._draw(screen)
                pygame.display.flip()
                clock.tick(FRAME_RATE)
        finally:
            pygame.quit()
        return exited_by_user

    def close(self) -> None:
        """Stop and destroy every sensor, including sensors from partial setup."""
        feeds = tuple(self._feeds)
        self._feeds.clear()
        errors: list[RuntimeError] = []
        for feed in feeds:
            try:
                feed.sensor.stop()
            except RuntimeError as error:
                errors.append(error)
            try:
                feed.sensor.destroy()
            except RuntimeError as error:
                errors.append(error)
        if errors:
            raise DriverViewCleanupError(tuple(errors))

    def _draw(self, screen: pygame.Surface) -> None:
        front, left, right = self._feeds
        layout = calculate_layout(self._config)
        self._blit_image(
            screen,
            front.latest_image,
            self._config.window_size,
            layout.front_position,
        )
        if self._hud_enabled and self._cockpit_view:
            velocity = self._hero.get_velocity()
            control = self._hero.get_control()
            toast_alpha: int | None = None
            if self._mode_toast_started_at is not None:
                elapsed = time.monotonic() - self._mode_toast_started_at
                if elapsed < self._config.hud_mode_toast_duration:
                    fade_duration = self._config.hud_mode_toast_duration * 0.35
                    fade_elapsed = max(
                        0.0,
                        elapsed - (self._config.hud_mode_toast_duration - fade_duration),
                    )
                    toast_alpha = round(255 * (1.0 - fade_elapsed / fade_duration))
                else:
                    self._mode_toast_started_at = None
            self._hud_renderer.draw(
                screen,
                HudState(
                    speed_kmh=calculate_speed_kmh(
                        velocity.x, velocity.y, velocity.z
                    ),
                    gear=gear_from_control(
                        reverse=control.reverse, gear=control.gear
                    ),
                    driving_mode=self.driving_mode,
                    mode_toast_alpha=toast_alpha,
                ),
            )
        self._blit_image(
            screen,
            left.latest_image,
            self._config.mirror_size,
            layout.left_mirror_position,
            flip_horizontal=True,
        )
        self._blit_image(
            screen,
            right.latest_image,
            self._config.mirror_size,
            layout.right_mirror_position,
            flip_horizontal=True,
        )

    def _handle_mode_events(self, events: Sequence[pygame.event.Event]) -> None:
        for event in events:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_p:
                was_manual = self.driving_mode is DrivingMode.MANUAL
                if was_manual:
                    self._reset_manual_control()
                mode = self._driving_mode_controller.toggle()
                if not was_manual:
                    self._reset_manual_control()
                if self._hud_enabled:
                    self._mode_toast_started_at = time.monotonic()
                print(f"[driver-view] driving-mode={mode.value}")
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_h:
                self._hud_enabled = not self._hud_enabled
                if not self._hud_enabled:
                    self._mode_toast_started_at = None
                print(f"[driver-view] hud={'ON' if self._hud_enabled else 'OFF'}")
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_v:
                self._cockpit_view = not self._cockpit_view
                transform = self._config.cockpit_transform if self._cockpit_view else self._config.front_transform
                fov = self._config.cockpit_fov if self._cockpit_view else self._config.fov
                front = self._feeds[0]
                front.sensor.stop()
                front.sensor.destroy()
                blueprint = self._world.get_blueprint_library().find(CAMERA_BLUEPRINT_ID)
                blueprint.set_attribute("image_size_x", str(self._config.front_resolution[0]))
                blueprint.set_attribute("image_size_y", str(self._config.front_resolution[1]))
                blueprint.set_attribute("fov", str(fov))
                blueprint.set_attribute("exposure_compensation", "0.5") # 새롭게 추가한 부분
                # 현재 v를 누르면 front camera를 삭제하고 다시 만들기 때문
                # 0.5로 했는데도 어두우면 0.8, 1.0 등으로 올려서 테스트해보기


                front.sensor = self._world.spawn_actor(blueprint, transform.to_carla(), attach_to=self._hero)
                front.sensor.listen(front.receive)
                print(f"[driver-view] view={'COCKPIT' if self._cockpit_view else 'DRIVER'}")

    def _apply_manual_control(self) -> None:
        if self.driving_mode is not DrivingMode.MANUAL:
            return
        keys = pygame.key.get_pressed()
        target_steer = float(keys[pygame.K_d]) - float(keys[pygame.K_a])
        self._steer = max(self._steer - STEER_INCREMENT, min(self._steer + STEER_INCREMENT, target_steer))
        control = carla.VehicleControl(
            throttle=1.0 if keys[pygame.K_w] else 0.0, brake=1.0 if keys[pygame.K_s] else 0.0,
            steer=self._steer, hand_brake=keys[pygame.K_SPACE],
        )
        self._hero.apply_control(control)

    def _reset_manual_control(self) -> None:
        self._steer = 0.0
        self._hero.apply_control(carla.VehicleControl())

    @staticmethod
    def _blit_image(
        screen: pygame.Surface,
        image: carla.Image | None,
        size: tuple[int, int],
        position: tuple[int, int],
        flip_horizontal: bool = False,
    ) -> None:
        if image is None:
            return
        surface = pygame.image.frombuffer(
            image.raw_data, (image.width, image.height), "BGRA"
        )
        scaled_surface = pygame.transform.smoothscale(surface, size)
        if flip_horizontal:
            scaled_surface = pygame.transform.flip(scaled_surface, True, False)
        screen.blit(scaled_surface, position)

    @staticmethod
    def _exit_requested(events: Sequence[pygame.event.Event]) -> bool:
        return any(
            event.type == pygame.QUIT
            or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
            for event in events
        )
