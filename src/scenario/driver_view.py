from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import carla
import pygame

from src.experiment.automation import AutomationState
from src.experiment.automation_interaction import DriverInput
from src.scenario.camera_feed import (
    CameraFeed,
    DriverViewPerformanceObserver,
)
from src.scenario.driver_hud import (
    DriverHudRenderer,
    HudRenderer,
    HudState,
    calculate_speed_kmh,
    gear_from_control,
)
from src.scenario.mirror_layout import MirrorLayout as DriverViewLayout
from src.scenario.mirror_layout import MirrorLayoutSpec, calculate_mirror_layout
from src.scenario.turn_signal_audio import TurnSignalAudio
from src.vehicle.driving_mode import DrivingMode, DrivingModeController

CAMERA_BLUEPRINT_ID: Final = "sensor.camera.rgb"
CAMERA_FOV: Final = 100.0
FRAME_RATE: Final = 60
STEER_INCREMENT: Final = 0.04


class RuntimeIterationScheduler(Protocol):
    """Run optional work once during an existing driver-view iteration."""

    def update(self) -> bool: ...


class RuntimeDriverInputObserver(Protocol):
    def update(self, driver_input: DriverInput) -> AutomationState: ...


@runtime_checkable
class ResearchPostControlObserver(Protocol):
    def after_control_applied(self) -> None: ...


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
class RearMirrorConfig:
    """Rear camera capture and screen-layout settings."""

    resolution: tuple[int, int] = (640, 180)
    size: tuple[int, int] = (320, 90)
    fov: float = 90.0
    top_ratio: float = 5.0 / 36.0
    transform: CameraTransform = CameraTransform(
        x=-2.5,
        y=0.0,
        z=1.3,
        yaw=180.0,
    )


@dataclass(frozen=True, slots=True)
class DriverViewConfig:
    """Small set of easily adjustable driver-view prototype settings."""

    window_size: tuple[int, int] = (1280, 720)
    front_resolution: tuple[int, int] = (1280, 720)
    mirror_resolution: tuple[int, int] = (480, 270)
    mirror_size: tuple[int, int] = (240, 135)
    mirror_margin: int = 24
    fov: float = CAMERA_FOV
    cockpit_fov: float = 105.0
    mirror_fov: float = 100.0
    rear_mirror: RearMirrorConfig = RearMirrorConfig()
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


class DriverViewCleanupError(RuntimeError):
    """Raised after every camera cleanup operation was attempted but one failed."""

    def __init__(self, errors: tuple[RuntimeError, ...]) -> None:
        self.errors = errors
        super().__init__("Unable to clean up all driver-view cameras.")


def calculate_layout(config: DriverViewConfig) -> DriverViewLayout:
    """Derive all mirror rectangles from the current window configuration."""
    return calculate_mirror_layout(
        MirrorLayoutSpec(
            window_size=config.window_size,
            side_size=config.mirror_size,
            rear_size=config.rear_mirror.size,
            edge_margin=config.mirror_margin,
            rear_top_ratio=config.rear_mirror.top_ratio,
        )
    )


class DriverView:
    """Attach and compose a forward feed with three rear-facing mirror feeds."""

    def __init__(
        self,
        world: carla.World,
        hero: carla.Actor,
        config: DriverViewConfig | None = None,
        *,
        performance_observer: DriverViewPerformanceObserver | None = None,
    ) -> None:
        self._world = world
        self._hero = hero
        self._config = config or DriverViewConfig()
        self._driving_mode_controller = DrivingModeController(
            hero, self._config.initial_driving_mode
        )
        self._steer = 0.0
        self._pending_driver_input: DriverInput | None = None
        self._cockpit_view = False
        self._hud_enabled = True
        self._mode_toast_started_at: float | None = None
        self._hud_renderer: HudRenderer = DriverHudRenderer(
            self._config.window_size,
            anchor_ratio=self._config.hud_anchor_ratio,
            toast_y_ratio=self._config.hud_toast_y_ratio,
        )
        self._turn_signal_audio = TurnSignalAudio()
        self._feeds: list[CameraFeed] = []
        self._performance_observer = performance_observer

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
        """Create and attach the front and three mirror RGB cameras."""
        mounts: Sequence[tuple[str, CameraTransform, tuple[int, int], float]] = (
            ("front", self._config.front_transform, self._config.front_resolution, self._config.fov),
            (
                "rear",
                self._config.rear_mirror.transform,
                self._config.rear_mirror.resolution,
                self._config.rear_mirror.fov,
            ),
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
            if self._performance_observer is not None:
                feed.receipt_clock = self._performance_observer.timestamp
            self._feeds.append(feed)
            sensor.listen(feed.receive)

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
    ) -> bool:
        """Show the composed view until duration elapses or the user exits it."""
        pygame.display.init()
        try:
            screen = pygame.display.set_mode(self._config.window_size)
            pygame.display.set_caption("CARLA driver view")
            clock = pygame.time.Clock()
            started_at = time.monotonic()
            exited_by_user = False
            while time.monotonic() - started_at < duration:
                observer = self._performance_observer
                loop_started_at = observer.timestamp() if observer is not None else 0.0
                self._begin_iteration()
                events = pygame.event.get()
                if input_observer is None:
                    self._handle_mode_events(events)
                if self._exit_requested(events):
                    exited_by_user = True
                    break
                if input_observer is not None:
                    input_started_at = self._interaction_timestamp()
                    self._pending_driver_input = self._collect_driver_input(events)
                    input_observer.update(self._pending_driver_input)
                    self._record_interaction_duration("input_observer", input_started_at)
                scheduler_started_at = (
                    observer.timestamp() if observer is not None else 0.0
                )
                control_applied = scheduler.update() if scheduler is not None else False
                if observer is not None:
                    observer.record_duration(
                        "scheduler", observer.timestamp() - scheduler_started_at
                    )
                if not control_applied:
                    self._apply_manual_control()
                if input_observer is not None:
                    self._handle_mode_events(events)
                if isinstance(input_observer, ResearchPostControlObserver):
                    post_started_at = self._interaction_timestamp()
                    input_observer.after_control_applied()
                    self._record_interaction_duration("post_control", post_started_at)
                audio_started_at = observer.timestamp() if observer is not None else 0.0
                self._update_turn_signal_audio()
                if observer is not None:
                    observer.record_duration(
                        "audio", observer.timestamp() - audio_started_at
                    )
                self._prepare_frame()
                draw_started_at = observer.timestamp() if observer is not None else 0.0
                self._draw(screen)
                if observer is not None:
                    observer.record_duration(
                        "draw", observer.timestamp() - draw_started_at
                    )
                flip_started_at = observer.timestamp() if observer is not None else 0.0
                pygame.display.flip()
                self._after_display_flip()
                if observer is not None:
                    observer.record_duration(
                        "display_flip", observer.timestamp() - flip_started_at
                    )
                limiter_started_at = (
                    observer.timestamp() if observer is not None else 0.0
                )
                clock.tick(FRAME_RATE)
                if observer is not None:
                    observer.record_duration(
                        "fps_limiter", observer.timestamp() - limiter_started_at
                    )
                    observer.record_duration(
                        "driver_loop", observer.timestamp() - loop_started_at
                    )
        finally:
            try:
                self._turn_signal_audio.close()
            finally:
                pygame.quit()
        return exited_by_user

    def _begin_iteration(self) -> None:
        return

    def _prepare_frame(self) -> None:
        return

    def _after_display_flip(self) -> None:
        return

    def _interaction_timestamp(self) -> float:
        observer = self._performance_observer
        return observer.timestamp() if observer is not None else time.perf_counter()

    def _record_interaction_duration(self, operation: str, started_at: float) -> None:
        if self._performance_observer is not None:
            self._performance_observer.record_duration(
                operation, self._interaction_timestamp() - started_at
            )

    def close(self) -> None:
        """Stop and destroy every sensor, including sensors from partial setup."""
        self._turn_signal_audio.close()
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
        front = self._feeds[0]
        layout = calculate_layout(self._config)
        self._blit_image(
            screen,
            front,
            self._config.window_size,
            layout.front_position,
        )
        hud_driving_mode, hud_mode_label = self._hud_mode()
        if self._hud_enabled and (self._cockpit_view or hud_mode_label is not None):
            velocity = self._hero.get_velocity()
            control = self._hero.get_control()
            toast_alpha = 255 if hud_mode_label is not None else None
            if hud_mode_label is None and self._mode_toast_started_at is not None:
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
                    driving_mode=hud_driving_mode,
                    mode_toast_alpha=toast_alpha,
                    mode_label=hud_mode_label,
                ),
            )
        for feed in self._feeds[1:]:
            mirror_rect = layout.rect_for(feed.role)
            if mirror_rect is not None:
                self._blit_image(
                    screen,
                    feed,
                    mirror_rect.size,
                    mirror_rect.position,
                    flip_horizontal=True,
                )

    def _hud_mode(self) -> tuple[DrivingMode, str | None]:
        return self.driving_mode, None

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
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_z, pygame.K_x):
                if self.driving_mode is DrivingMode.MANUAL:
                    selected = (
                        carla.VehicleLightState.LeftBlinker
                        if event.key == pygame.K_z
                        else carla.VehicleLightState.RightBlinker
                    )
                    self._toggle_turn_signal(selected)
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

    def _toggle_turn_signal(self, selected: carla.VehicleLightState) -> None:
        current = self._hero.get_light_state()
        blinkers = (
            carla.VehicleLightState.LeftBlinker
            | carla.VehicleLightState.RightBlinker
        )
        without_blinkers = current & ~blinkers
        updated = (
            without_blinkers
            if current & selected
            else without_blinkers | selected
        )
        self._hero.set_light_state(carla.VehicleLightState(updated))

    def _update_turn_signal_audio(self) -> None:
        observer = self._performance_observer
        light_state_started_at = observer.timestamp() if observer is not None else 0.0
        lights = self._hero.get_light_state()
        if observer is not None:
            observer.record_turn_signal_audio_duration(
                "light_state_rpc",
                observer.timestamp() - light_state_started_at,
            )
        blinkers = (
            carla.VehicleLightState.LeftBlinker
            | carla.VehicleLightState.RightBlinker
        )
        audio_now = time.monotonic()
        audio_update_started_at = observer.timestamp() if observer is not None else 0.0
        self._turn_signal_audio.update(
            active=bool(lights & blinkers),
            now=audio_now,
        )
        if observer is not None:
            observer.record_turn_signal_audio_duration(
                "audio_update",
                observer.timestamp() - audio_update_started_at,
            )

    def _apply_manual_control(self) -> None:
        if self.driving_mode is not DrivingMode.MANUAL:
            return
        driver_input = self._pending_driver_input or self._collect_driver_input(())
        self._pending_driver_input = None
        control = carla.VehicleControl(
            throttle=driver_input.throttle,
            brake=driver_input.brake,
            steer=driver_input.steering,
            hand_brake=driver_input.hand_brake,
        )
        self._hero.apply_control(control)

    def _collect_driver_input(
        self, events: Sequence[pygame.event.Event]
    ) -> DriverInput:
        keys = pygame.key.get_pressed()
        target_steer = float(keys[pygame.K_d]) - float(keys[pygame.K_a])
        self._steer = max(self._steer - STEER_INCREMENT, min(self._steer + STEER_INCREMENT, target_steer))
        noa_button_pressed = any(
            event.type == pygame.KEYDOWN and event.key == pygame.K_n
            for event in events
        )
        return DriverInput(
            throttle=1.0 if keys[pygame.K_w] else 0.0,
            brake=1.0 if keys[pygame.K_s] else 0.0,
            steering=self._steer,
            steering_engaged=bool(keys[pygame.K_a] or keys[pygame.K_d]),
            activation_requested=noa_button_pressed,
            deactivation_requested=noa_button_pressed,
            hand_brake=bool(keys[pygame.K_SPACE]),
        )

    def _reset_manual_control(self) -> None:
        self._steer = 0.0
        self._hero.apply_control(carla.VehicleControl())

    def _blit_image(
        self,
        screen: pygame.Surface,
        feed: CameraFeed,
        size: tuple[int, int],
        position: tuple[int, int],
        flip_horizontal: bool = False,
    ) -> None:
        snapshot = feed.snapshot()
        observer = self._performance_observer
        if observer is not None:
            observer.record_camera_preparation(
                feed.role,
                snapshot,
                observer.timestamp(),
            )
        image = snapshot.image
        if image is None:
            return
        if observer is None:
            surface = pygame.image.frombuffer(
                image.raw_data, (image.width, image.height), "BGRA"
            )
            scaled_surface = pygame.transform.smoothscale(surface, size)
            if flip_horizontal:
                scaled_surface = pygame.transform.flip(scaled_surface, True, False)
            screen.blit(scaled_surface, position)
            return
        started_at = observer.timestamp()
        surface = pygame.image.frombuffer(
            image.raw_data, (image.width, image.height), "BGRA"
        )
        observer.record_duration(
            f"conversion.{feed.role}", observer.timestamp() - started_at
        )
        started_at = observer.timestamp()
        scaled_surface = pygame.transform.smoothscale(surface, size)
        observer.record_duration(
            f"resize.{feed.role}", observer.timestamp() - started_at
        )
        if flip_horizontal:
            started_at = observer.timestamp()
            scaled_surface = pygame.transform.flip(scaled_surface, True, False)
            observer.record_duration(
                f"mirror_flip.{feed.role}", observer.timestamp() - started_at
            )
        started_at = observer.timestamp()
        screen.blit(scaled_surface, position)
        observer.record_duration(
            f"blit.{feed.role}", observer.timestamp() - started_at
        )

    @staticmethod
    def _exit_requested(events: Sequence[pygame.event.Event]) -> bool:
        return any(
            event.type == pygame.QUIT
            or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
            for event in events
        )
