from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import carla
import pygame

from src.experiment.automation import AutomationState
from src.experiment.automation_interaction import DriverInput
from src.scenario.active_scene import ActiveDriveScene
from src.scenario.camera_feed import (
    CameraFeed,
    CameraFeedSnapshot,
    DisplayedCameraFrame,
    DriverViewPerformanceObserver,
)
from src.scenario.driver_hud import (
    DriverHudRenderer,
    HudRenderer,
    HudState,
    calculate_speed_kmh,
    gear_from_control,
)
from src.scenario.driver_view_presentation import DriverViewPresentation
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


class RuntimeFrameContinuation(Protocol):
    def start(self) -> None: ...

    def after_control_applied(self, driver_input: DriverInput) -> bool: ...

    def finalize(self, reason: str, error: BaseException | None = None) -> None: ...


type RuntimeContinuationFactory = Callable[[], RuntimeFrameContinuation | None]


@runtime_checkable
class RuntimeDisplayContinuation(Protocol):
    def after_display_completed(self, frame: DisplayedCameraFrame | None) -> bool: ...


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
    rear_mirror_enabled: bool = False
    hud_anchor_ratio: tuple[float, float] = (0.62, 0.62)
    hud_toast_y_ratio: float = 0.58
    hud_mode_toast_duration: float = 1.75
    initial_driving_mode: DrivingMode = DrivingMode.AUTONOMOUS
    initial_cockpit_view: bool = False
    front_transform: CameraTransform = CameraTransform(x=1.4, y=0.0, z=1.3, pitch=-2.0)

    # 콕핏 뷰 카메라 위치 조정
    # cockpit_transform: CameraTransform = CameraTransform(x=0.2, y=-0.35, z=1.30, pitch=-1.5)
    # cockpit_transform: CameraTransform = CameraTransform(x=0.10, y=-0.35, z=1.20, pitch=-1.5)

    # 각 변수의 의미:
    #  x: 차량 전방으로부터의 거리, x값이 커질수록 차량 전방으로 카메라가 이동
    # y: 차량 좌우로부터의 거리,  y값이 음수이면 왼쪽, 양수이면 오른쪽으로 카메라가 이동
    # z: 차량 바닥으로부터의 높이, z값이 커질수록 카메라가 위로 올라감
    # pitch: 카메라의 상하 각도, 음수로 갈 수록 아래를 바라보게 됨
    cockpit_transform: CameraTransform = CameraTransform(
        x=0.25, y=-0.35, z=1.20, pitch=1
    )

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
    """Attach and compose a forward feed with the configured mirror feeds."""

    def __init__(
        self,
        world: carla.World,
        hero: carla.Actor,
        config: DriverViewConfig | None = None,
        *,
        performance_observer: DriverViewPerformanceObserver | None = None,
        read_only: bool = False,
        training_early_finish: bool = False,
        traffic_manager_port: int | None = None,
    ) -> None:
        self._world = world
        self._hero = hero
        self._config = config or DriverViewConfig()
        self._driving_mode_controller = (
            None
            if read_only
            else DrivingModeController(
                hero,
                self._config.initial_driving_mode,
                traffic_manager_port=traffic_manager_port,
            )
        )
        self._steer = 0.0
        self._pending_driver_input: DriverInput | None = None
        self._cockpit_view = self._config.initial_cockpit_view
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
        self._training_early_finish = training_early_finish
        self._training_completed_early = False

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
        if self._driving_mode_controller is None:
            raise RuntimeError("read-only view does not own a driving mode")
        return self._driving_mode_controller.mode

    @property
    def hud_enabled(self) -> bool:
        """Return whether the cockpit HUD is enabled."""
        return self._hud_enabled

    @property
    def training_completed_early(self) -> bool:
        return self._training_completed_early

    def attach(self) -> None:
        """Create and attach the active RGB cameras."""
        front_transform, front_fov = self._front_camera_settings()
        rear_mounts = (
            (
                (
                    "rear",
                    self._config.rear_mirror.transform,
                    self._config.rear_mirror.resolution,
                    self._config.rear_mirror.fov,
                ),
            )
            if self._config.rear_mirror_enabled
            else ()
        )
        mounts: Sequence[tuple[str, CameraTransform, tuple[int, int], float]] = (
            ("front", front_transform, self._config.front_resolution, front_fov),
            *rear_mounts,
            (
                "left",
                self._config.left_mirror_transform,
                self._config.mirror_resolution,
                self._config.mirror_fov,
            ),
            (
                "right",
                self._config.right_mirror_transform,
                self._config.mirror_resolution,
                self._config.mirror_fov,
            ),
        )
        blueprint = self._world.get_blueprint_library().find(CAMERA_BLUEPRINT_ID)
        for role, transform, resolution, fov in mounts:
            width, height = resolution
            blueprint.set_attribute("image_size_x", str(width))
            blueprint.set_attribute("image_size_y", str(height))
            blueprint.set_attribute("fov", str(fov))

            if (
                role == "front"
            ):  # 새로 추가한 부분, front 카메라의 exposure를 살짝 올려보기
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
                feed.performance_observer = self._performance_observer
            self._feeds.append(feed)
            sensor.listen(feed.receive)

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
        continuation_factory: RuntimeContinuationFactory | None = None,
        active_scene: ActiveDriveScene | None = None,
    ) -> bool:
        """Show the composed view until duration elapses or the user exits it."""
        if self._driving_mode_controller is None:
            raise RuntimeError("read-only view cannot run the vehicle-control loop")
        self._training_completed_early = False
        try:
            screen = self._open_display()
            self._initialize_input()
            presentation = DriverViewPresentation(self._config.window_size, screen)
            clock = pygame.time.Clock()
            started_at = (
                time.monotonic() if active_scene is None else active_scene.started_at
            )
            exited_by_user = False
            continuation: RuntimeFrameContinuation | None = None
            display_continuation: RuntimeDisplayContinuation | None = None
            self._capture_hazard_display = active_scene is not None
            self._hazard_front_frame = None
            continuation_finalized = False
            while True:
                boundary_events = None
                if continuation is None and time.monotonic() - started_at >= duration:
                    if continuation_factory is None:
                        break
                    # Honor a queued user exit before acquiring hazard resources.
                    # Reuse this poll below; do not consume non-exit input twice.
                    boundary_events = pygame.event.get()
                    if self._exit_requested(boundary_events):
                        exited_by_user = True
                        break
                    continuation = continuation_factory()
                    continuation_factory = None
                    if continuation is None:
                        break
                    continuation.start()
                    if isinstance(continuation, RuntimeDisplayContinuation):
                        display_continuation = continuation
                        self._capture_hazard_display = True
                observer = self._performance_observer
                loop_started_at = observer.timestamp() if observer is not None else 0.0
                self._begin_iteration()
                events_started_at = (
                    observer.timestamp() if observer is not None else 0.0
                )
                try:
                    events = (
                        pygame.event.get()
                        if boundary_events is None
                        else boundary_events
                    )
                    presentation.handle_events(events)
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "event_poll_and_presentation",
                            observer.timestamp() - events_started_at,
                        )
                if input_observer is None:
                    self._timed_mode_events(events)
                if self._exit_requested(events):
                    exited_by_user = True
                    if continuation is not None:
                        continuation_finalized = True
                        continuation.finalize("USER_EXIT")
                    break
                if continuation is None and self._training_finish_requested(events):
                    self._training_completed_early = True
                    break
                collected_input: DriverInput | None = None
                if (
                    input_observer is not None
                    or continuation is not None
                    or active_scene is not None
                ):
                    collection_started_at = self._interaction_timestamp()
                    poll_started_ns = (
                        time.monotonic_ns() if active_scene is not None else 0
                    )
                    try:
                        collected_input = self._collect_driver_input(events)
                        poll_completed_ns = (
                            time.monotonic_ns() if active_scene is not None else 0
                        )
                        self._pending_driver_input = collected_input
                    finally:
                        self._record_interaction_duration(
                            "input_collection", collection_started_at
                        )
                    if input_observer is not None:
                        input_started_at = self._interaction_timestamp()
                        try:
                            input_observer.update(collected_input)
                        finally:
                            self._record_interaction_duration(
                                "input_observer", input_started_at
                            )
                scheduler_started_at = self._interaction_timestamp()
                try:
                    control_applied = (
                        scheduler.update() if scheduler is not None else False
                    )
                finally:
                    self._record_interaction_duration("scheduler", scheduler_started_at)
                if not control_applied:
                    self._apply_manual_control()
                if input_observer is not None:
                    self._timed_mode_events(self._events_for_mode_handling(events))
                if isinstance(input_observer, ResearchPostControlObserver):
                    post_started_at = self._interaction_timestamp()
                    try:
                        input_observer.after_control_applied()
                    finally:
                        self._record_interaction_duration(
                            "post_control", post_started_at
                        )
                continuation_active = True
                if active_scene is not None and collected_input is not None:
                    active_scene.after_control(
                        collected_input, poll_started_ns, poll_completed_ns
                    )
                if continuation is not None:
                    if collected_input is None:
                        raise RuntimeError(
                            "continuation driver input was not collected"
                        )
                    continuation_active = continuation.after_control_applied(
                        collected_input
                    )
                audio_started_at = observer.timestamp() if observer is not None else 0.0
                self._update_turn_signal_audio()
                if observer is not None:
                    observer.record_duration(
                        "audio", observer.timestamp() - audio_started_at
                    )
                prepare_started_at = (
                    observer.timestamp() if observer is not None else 0.0
                )
                try:
                    self._prepare_frame()
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "frame_prepare", observer.timestamp() - prepare_started_at
                        )
                draw_started_at = observer.timestamp() if observer is not None else 0.0
                if self._capture_hazard_display:
                    self._hazard_front_frame = None
                try:
                    self._draw(presentation.canvas)
                    presentation.present()
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "draw", observer.timestamp() - draw_started_at
                        )
                flip_started_at = observer.timestamp() if observer is not None else 0.0
                try:
                    pygame.display.flip()
                    flip_completed_ns = (
                        time.monotonic_ns()
                        if display_continuation is not None or active_scene is not None
                        else None
                    )
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "display_flip", observer.timestamp() - flip_started_at
                        )
                if observer is not None:
                    observer.record_completed_display_flip()
                after_flip_started_at = (
                    observer.timestamp() if observer is not None else 0.0
                )
                try:
                    self._after_display_flip()
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "after_display_flip",
                            observer.timestamp() - after_flip_started_at,
                        )
                if display_continuation is not None or active_scene is not None:
                    assert flip_completed_ns is not None
                    selected = self._hazard_front_frame
                    frame = (
                        None
                        if selected is None
                        else DisplayedCameraFrame(
                            selected[0],
                            selected[1],
                            selected[2],
                            selected[3],
                            flip_completed_ns,
                            selected[4],
                        )
                    )
                    if display_continuation is not None:
                        continuation_active = (
                            display_continuation.after_display_completed(frame)
                            and continuation_active
                        )
                    if active_scene is not None:
                        active_scene.after_display_completed(frame)
                limiter_started_at = (
                    observer.timestamp() if observer is not None else 0.0
                )
                try:
                    clock.tick(FRAME_RATE)
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "fps_limiter", observer.timestamp() - limiter_started_at
                        )
                if observer is not None:
                    observer.record_duration(
                        "driver_loop", observer.timestamp() - loop_started_at
                    )
                self._complete_iteration()
                if continuation is not None and not continuation_active:
                    continuation_finalized = True
                    continuation.finalize("TERMINAL")
                    break
        except BaseException as error:
            if (
                "continuation" in locals()
                and continuation is not None
                and not continuation_finalized
            ):
                continuation_finalized = True
                continuation.finalize("ERROR", error)
            raise
        finally:
            self._capture_hazard_display = False
            self._hazard_front_frame = None
            try:
                try:
                    self._turn_signal_audio.close()
                finally:
                    self._close_input()
            finally:
                pygame.quit()
        return exited_by_user

    def _begin_iteration(self) -> None:
        return

    def _timed_mode_events(self, events: Sequence[pygame.event.Event]) -> None:
        observer = self._performance_observer
        started_at = observer.timestamp() if observer is not None else 0.0
        try:
            self._handle_mode_events(events)
        finally:
            if observer is not None:
                observer.record_duration(
                    "mode_events", observer.timestamp() - started_at
                )

    def _open_display(self) -> pygame.Surface:
        pygame.display.init()
        screen = pygame.display.get_surface()
        if screen is None or screen.get_size() != self._config.window_size:
            screen = pygame.display.set_mode(self._config.window_size)
        pygame.display.set_caption("CARLA driver view")
        return screen

    def _initialize_input(self) -> None:
        return

    def _close_input(self) -> None:
        return

    def _events_for_mode_handling(
        self,
        events: Sequence[pygame.event.Event],
    ) -> Sequence[pygame.event.Event]:
        return events

    def _prepare_frame(self) -> None:
        return

    def _after_display_flip(self) -> None:
        return

    def _complete_iteration(self) -> None:
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
                        elapsed
                        - (self._config.hud_mode_toast_duration - fade_duration),
                    )
                    toast_alpha = round(255 * (1.0 - fade_elapsed / fade_duration))
                else:
                    self._mode_toast_started_at = None
            self._hud_renderer.draw(
                screen,
                HudState(
                    speed_kmh=calculate_speed_kmh(velocity.x, velocity.y, velocity.z),
                    gear=gear_from_control(reverse=control.reverse, gear=control.gear),
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

    def draw_preview(self, screen: pygame.Surface) -> None:
        if self._driving_mode_controller is not None:
            raise RuntimeError("draw_preview requires a read-only view")
        self._cockpit_view = True
        self._draw(screen)

    def _hud_mode(self) -> tuple[DrivingMode, str | None]:
        if self._driving_mode_controller is None:
            return DrivingMode.MANUAL, None
        return self.driving_mode, None

    def _handle_mode_events(self, events: Sequence[pygame.event.Event]) -> None:
        if self._driving_mode_controller is None:
            return
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
                transform, fov = self._front_camera_settings()
                front = self._feeds[0]
                front.sensor.stop()
                front.sensor.destroy()
                blueprint = self._world.get_blueprint_library().find(
                    CAMERA_BLUEPRINT_ID
                )
                blueprint.set_attribute(
                    "image_size_x", str(self._config.front_resolution[0])
                )
                blueprint.set_attribute(
                    "image_size_y", str(self._config.front_resolution[1])
                )
                blueprint.set_attribute("fov", str(fov))
                blueprint.set_attribute(
                    "exposure_compensation", "0.5"
                )  # 새롭게 추가한 부분
                # 현재 v를 누르면 front camera를 삭제하고 다시 만들기 때문
                # 0.5로 했는데도 어두우면 0.8, 1.0 등으로 올려서 테스트해보기

                front.sensor = self._world.spawn_actor(
                    blueprint, transform.to_carla(), attach_to=self._hero
                )
                front.sensor.listen(front.receive)
                print(
                    f"[driver-view] view={'COCKPIT' if self._cockpit_view else 'DRIVER'}"
                )

    def _front_camera_settings(self) -> tuple[CameraTransform, float]:
        if self._cockpit_view:
            return self._config.cockpit_transform, self._config.cockpit_fov
        return self._config.front_transform, self._config.fov

    def _toggle_turn_signal(self, selected: carla.VehicleLightState) -> None:
        if self._driving_mode_controller is None:
            return
        current = self._hero.get_light_state()
        blinkers = (
            carla.VehicleLightState.LeftBlinker | carla.VehicleLightState.RightBlinker
        )
        without_blinkers = current & ~blinkers
        updated = (
            without_blinkers if current & selected else without_blinkers | selected
        )
        self._hero.set_light_state(carla.VehicleLightState(updated))

    def _update_turn_signal_audio(self) -> None:
        observer = self._performance_observer
        light_state_started_at = observer.timestamp() if observer is not None else 0.0
        try:
            lights = self._hero.get_light_state()
        finally:
            if observer is not None:
                observer.record_turn_signal_audio_duration(
                    "light_state_rpc",
                    observer.timestamp() - light_state_started_at,
                )
        blinkers = (
            carla.VehicleLightState.LeftBlinker | carla.VehicleLightState.RightBlinker
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
        if self._driving_mode_controller is None:
            return
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
        self._send_control(control)

    def _send_control(self, control: carla.VehicleControl) -> None:
        self._hero.apply_control(control)

    def _collect_driver_input(
        self, events: Sequence[pygame.event.Event]
    ) -> DriverInput:
        keys = pygame.key.get_pressed()
        target_steer = float(keys[pygame.K_d]) - float(keys[pygame.K_a])
        self._steer = max(
            self._steer - STEER_INCREMENT,
            min(self._steer + STEER_INCREMENT, target_steer),
        )
        noa_button_pressed = any(
            event.type == pygame.KEYDOWN and event.key == pygame.K_n for event in events
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
        if self._driving_mode_controller is None:
            return
        self._steer = 0.0
        self._send_control(carla.VehicleControl())

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
            self._remember_hazard_front(feed, snapshot)
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
        self._remember_hazard_front(feed, snapshot)
        observer.record_duration(f"blit.{feed.role}", observer.timestamp() - started_at)

    def _remember_hazard_front(
        self, feed: CameraFeed, snapshot: CameraFeedSnapshot
    ) -> None:
        if getattr(self, "_capture_hazard_display", False) and feed.role == "front":
            self._hazard_front_frame = (
                feed.sensor.id,
                snapshot.last_sensor_frame,
                snapshot.last_sensor_timestamp_seconds,
                snapshot.last_host_receipt_seconds,
                getattr(snapshot.image, "transform", None),
            )

    @staticmethod
    def _exit_requested(events: Sequence[pygame.event.Event]) -> bool:
        return any(
            event.type == pygame.QUIT
            or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
            for event in events
        )

    def _training_finish_requested(self, events: Sequence[pygame.event.Event]) -> bool:
        return self._training_early_finish and any(
            event.type == pygame.KEYDOWN and event.key == pygame.K_F8
            for event in events
        )
