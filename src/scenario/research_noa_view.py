from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from time import perf_counter
from typing import assert_never

import carla
import pygame

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.scenario.driver_view import (
    CAMERA_BLUEPRINT_ID,
    CameraFeed,
    DriverView,
    DriverViewConfig,
    RuntimeDriverInputObserver,
    RuntimeIterationScheduler,
)
from src.scenario.research_camera_diagnostics import ResearchCameraDiagnostics
from src.scenario.research_camera_metadata import ResearchCameraRunMeasurements
from src.scenario.research_exit_view import (
    ExitViewBinding,
    KeyEvent,
    ResearchExitViewController,
)
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.vehicle.driving_mode import DrivingMode


@dataclass(frozen=True, slots=True)
class ResearchDriverViewConfig(DriverViewConfig):
    initial_driving_mode: DrivingMode = DrivingMode.MANUAL
    front_camera_only: bool = False
    camera_diagnostics: bool = False
    run_id: str | None = None
    driver_input_source: Callable[[], DriverInput] | None = None
    manual_control_limits: ResearchManualControlLimits | None = None
    noa_toggle_key: int = pygame.K_n
    automation_state_source: Callable[[], AutomationState] | None = None


@dataclass(frozen=True, slots=True)
class ResearchManualControlLimits:
    max_throttle: float
    max_brake: float
    max_steering: float


class ResearchDriverView(DriverView):
    _diagnostics: ResearchNoADiagnostics | None = None

    def __init__(
        self,
        world: carla.World,
        hero: carla.Actor,
        config: ResearchDriverViewConfig | None = None,
    ) -> None:
        self._research_config = config or ResearchDriverViewConfig()
        self._camera_diagnostics = (
            ResearchCameraDiagnostics(
                run_id=self._research_config.run_id,
                composition=(
                    "front_only_1_rgb"
                    if self._research_config.front_camera_only
                    else "front_rear_left_right_4_rgb"
                ),
            )
            if self._research_config.camera_diagnostics
            else None
        )
        self._exit_view = ResearchExitViewController(self._research_config.window_size)
        super().__init__(
            world,
            hero,
            self._research_config,
            performance_observer=self._camera_diagnostics,
        )

    def set_exit_view_binding(self, binding: ExitViewBinding | None) -> None:
        self._exit_view.bind(binding)

    def _prepare_frame(self) -> None:
        self._exit_view.prepare()

    def attach(self) -> None:
        if not self._research_config.front_camera_only:
            super().attach()
            return
        blueprint = self._world.get_blueprint_library().find(CAMERA_BLUEPRINT_ID)
        width, height = self._research_config.front_resolution
        blueprint.set_attribute("image_size_x", str(width))
        blueprint.set_attribute("image_size_y", str(height))
        blueprint.set_attribute("fov", str(self._research_config.fov))
        blueprint.set_attribute("exposure_compensation", "0.5")
        sensor = self._world.spawn_actor(
            blueprint,
            self._research_config.front_transform.to_carla(),
            attach_to=self._hero,
        )
        feed = CameraFeed(role="front", sensor=sensor)
        feed.receipt_clock = (
            self._camera_diagnostics.timestamp
            if self._camera_diagnostics is not None
            else perf_counter
        )
        self._feeds.append(feed)
        sensor.listen(feed.receive)

    def capture_camera_diagnostics_environment(self, world: carla.World) -> None:
        if self._camera_diagnostics is not None:
            self._camera_diagnostics.capture_environment(world)

    def camera_diagnostics_json(
        self,
        measurements: ResearchCameraRunMeasurements | None = None,
    ) -> str | None:
        if self._camera_diagnostics is None:
            return None
        return self._camera_diagnostics.to_json(
            self.feeds,
            self._research_config,
            measurements,
        )

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
    ) -> bool:
        self._diagnostics = diagnostics
        try:
            return super().run(duration, scheduler, input_observer)
        finally:
            if self._diagnostics is not None:
                self._diagnostics.discard_incomplete_driver_loop()
            self._diagnostics = None

    def _begin_iteration(self) -> None:
        if self._diagnostics is not None:
            self._diagnostics.begin_driver_loop()

    def _interaction_timestamp(self) -> float:
        if self._diagnostics is not None:
            return self._diagnostics.timestamp()
        return super()._interaction_timestamp()

    def _record_interaction_duration(self, operation: str, started_at: float) -> None:
        if self._diagnostics is not None:
            self._diagnostics.record_operation_duration(
                operation, self._diagnostics.timestamp() - started_at
            )
        super()._record_interaction_duration(operation, started_at)

    def _handle_mode_events(self, events: list[KeyEvent]) -> None:
        filtered_events = [
            event
            for event in events
            if not (event.type == pygame.KEYDOWN and event.key == pygame.K_p)
        ]
        super()._handle_mode_events(filtered_events)

    def _draw(self, screen: pygame.Surface) -> None:
        diagnostics = self._diagnostics
        if diagnostics is None:
            super()._draw(screen)
        else:
            started_at = diagnostics.timestamp()
            super()._draw(screen)
            diagnostics.record_render_duration(diagnostics.timestamp() - started_at)
        self._exit_view.draw(screen, hud_enabled=self._hud_enabled)

    def _after_display_flip(self) -> None:
        self._exit_view.mark_after_flip()

    def _hud_mode(self) -> tuple[DrivingMode, str | None]:
        source = self._research_config.automation_state_source
        if source is None:
            return super()._hud_mode()
        match source().control_mode:
            case DrivingControlMode.MANUAL:
                return DrivingMode.MANUAL, DrivingControlMode.MANUAL.value
            case DrivingControlMode.NOA_ACTIVE:
                return DrivingMode.AUTONOMOUS, DrivingControlMode.NOA_ACTIVE.value
            case unreachable:
                assert_never(unreachable)

    def _apply_manual_control(self) -> None:
        return

    def _collect_driver_input(self, events: list[KeyEvent]) -> DriverInput:
        source = self._research_config.driver_input_source
        if source is not None:
            return source()
        driver_input = super()._collect_driver_input(events)
        toggle_requested = any(
            event.type == pygame.KEYDOWN
            and event.key == self._research_config.noa_toggle_key
            for event in events
        )
        confirm_requested, reject_requested = self._exit_view.requests(events)
        return replace(
            driver_input,
            activation_requested=toggle_requested,
            deactivation_requested=toggle_requested,
            exit_confirm_requested=confirm_requested,
            exit_reject_requested=reject_requested,
        )


class ResearchLiveDriverView(ResearchDriverView):
    def _apply_manual_control(self) -> None:
        limits = self._research_config.manual_control_limits
        if limits is None:
            DriverView._apply_manual_control(self)
            return
        if self.driving_mode is not DrivingMode.MANUAL:
            return
        driver_input = self._pending_driver_input or self._collect_driver_input(())
        self._pending_driver_input = None
        brake = min(driver_input.brake, limits.max_brake)
        self._hero.apply_control(
            carla.VehicleControl(
                throttle=(
                    0.0
                    if brake > 0.0
                    else min(driver_input.throttle, limits.max_throttle)
                ),
                brake=brake,
                steer=min(
                    max(driver_input.steering, -limits.max_steering),
                    limits.max_steering,
                ),
                hand_brake=driver_input.hand_brake,
            )
        )
