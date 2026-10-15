from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import assert_never

import carla
import pygame

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.experiment.lane_change import LaneChangeDirection
from src.p8.tsxw_input import TsxwInput, TsxwLogEvent
from src.p8.tsxw_policy import (
    TsxwCommandState,
    TsxwConfig,
    coalesce_command_events,
    merge_driver_input,
)
from src.scenario.driver_view import (
    CAMERA_BLUEPRINT_ID,
    CameraFeed,
    DriverView,
    DriverViewConfig,
    RuntimeContinuationFactory,
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
from src.scenario.research_lane_change_input import LaneChangeRequestInput
from src.scenario.research_noa_control_delivery import ControlSender
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.vehicle.driving_mode import DrivingMode


@dataclass(frozen=True, slots=True)
class ResearchDriverViewConfig(DriverViewConfig):
    initial_driving_mode: DrivingMode = DrivingMode.MANUAL
    initial_cockpit_view: bool = True
    front_camera_only: bool = False
    camera_diagnostics: bool = False
    run_id: str | None = None
    driver_input_source: Callable[[], DriverInput] | None = None
    manual_control_limits: ResearchManualControlLimits | None = None
    noa_toggle_key: int = pygame.K_n
    automation_state_source: Callable[[], AutomationState] | None = None
    training_early_finish: bool = False
    traffic_manager_port: int | None = None
    control_sender: ControlSender | None = None
    tsxw_config: TsxwConfig | None = None
    tsxw_event_sink: Callable[[TsxwLogEvent], None] | None = None


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
                control_mode_source=(
                    None
                    if self._research_config.automation_state_source is None
                    else lambda: (
                        self._research_config.automation_state_source().control_mode
                    )
                ),
                composition=(
                    "front_only_1_rgb"
                    if self._research_config.front_camera_only
                    else (
                        "front_rear_left_right_4_rgb"
                        if self._research_config.rear_mirror_enabled
                        else "front_left_right_3_rgb"
                    )
                ),
            )
            if self._research_config.camera_diagnostics
            else None
        )
        self._exit_view = ResearchExitViewController(self._research_config.window_size)
        self._lane_change_input = LaneChangeRequestInput()
        self._accepted_signal_event_ids: frozenset[int] | None = None
        self._tsxw_input: TsxwInput | None = None
        self._tsxw_commands = TsxwCommandState()
        self._tsxw_mode_events: list[KeyEvent] | None = None
        super().__init__(
            world,
            hero,
            self._research_config,
            performance_observer=self._camera_diagnostics,
            training_early_finish=self._research_config.training_early_finish,
            traffic_manager_port=self._research_config.traffic_manager_port,
        )

    def _automation_owns_control(self) -> bool:
        source = self._research_config.automation_state_source
        return (
            source is not None
            and source().control_mode is DrivingControlMode.NOA_ACTIVE
        )

    def _send_control(self, control: carla.VehicleControl) -> None:
        sender = self._research_config.control_sender
        if sender is None:
            super()._send_control(control)
            return
        sender(control)

    def _reset_manual_control(self) -> None:
        if self._automation_owns_control():
            return
        super()._reset_manual_control()

    def set_exit_view_binding(self, binding: ExitViewBinding | None) -> None:
        self._exit_view.bind(binding)

    def _prepare_frame(self) -> None:
        self._exit_view.prepare()

    def _initialize_input(self) -> None:
        config = self._research_config.tsxw_config
        if config is not None:
            self._tsxw_input = TsxwInput.open(
                config,
                event_sink=self._research_config.tsxw_event_sink,
            )
            self._tsxw_commands = TsxwCommandState()
            self._tsxw_mode_events = None

    def _close_input(self) -> None:
        if self._tsxw_input is not None:
            self._tsxw_input.close()
            self._tsxw_input = None

    def _events_for_mode_handling(
        self,
        events: Sequence[KeyEvent],
    ) -> Sequence[KeyEvent]:
        if self._tsxw_mode_events is None:
            return events
        combined = self._tsxw_mode_events
        self._tsxw_mode_events = None
        return combined

    def attach(self) -> None:
        if not self._research_config.front_camera_only:
            super().attach()
            return
        blueprint = self._world.get_blueprint_library().find(CAMERA_BLUEPRINT_ID)
        width, height = self._research_config.front_resolution
        transform, fov = self._front_camera_settings()
        blueprint.set_attribute("image_size_x", str(width))
        blueprint.set_attribute("image_size_y", str(height))
        blueprint.set_attribute("fov", str(fov))
        blueprint.set_attribute("exposure_compensation", "0.5")
        sensor = self._world.spawn_actor(
            blueprint,
            transform.to_carla(),
            attach_to=self._hero,
        )
        feed = CameraFeed(role="front", sensor=sensor)
        feed.receipt_clock = (
            self._camera_diagnostics.timestamp
            if self._camera_diagnostics is not None
            else perf_counter
        )
        feed.performance_observer = self._camera_diagnostics
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

    def freeze_performance_measurement_window(self) -> None:
        if self._camera_diagnostics is not None:
            self._camera_diagnostics.freeze_measurement_window(self.feeds)

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
        continuation_factory: RuntimeContinuationFactory | None = None,
    ) -> bool:
        self._diagnostics = diagnostics
        if self._camera_diagnostics is not None:
            self._camera_diagnostics.begin_active_window(self.feeds)
        try:
            return super().run(
                duration,
                scheduler,
                input_observer,
                continuation_factory,
            )
        finally:
            if self._camera_diagnostics is not None:
                self._camera_diagnostics.end_active_window(
                    self.feeds,
                    complete=sys.exception() is None,
                )
            if self._diagnostics is not None:
                self._diagnostics.discard_incomplete_driver_loop()
            self._diagnostics = None

    def _begin_iteration(self) -> None:
        if self._diagnostics is not None:
            source = self._research_config.automation_state_source
            if source is not None:
                self._diagnostics.set_control_mode(source().control_mode)
            self._diagnostics.begin_driver_loop()

    def _complete_iteration(self) -> None:
        if self._diagnostics is not None:
            self._diagnostics.complete_driver_loop()

    def _interaction_timestamp(self) -> float:
        if self._diagnostics is not None:
            return self._diagnostics.timestamp()
        return super()._interaction_timestamp()

    def _record_interaction_duration(self, operation: str, started_at: float) -> None:
        if self._diagnostics is not None:
            source = self._research_config.automation_state_source
            if source is not None:
                self._diagnostics.set_control_mode(source().control_mode)
            self._diagnostics.record_operation_duration(
                operation, self._diagnostics.timestamp() - started_at
            )
        super()._record_interaction_duration(operation, started_at)

    def _handle_mode_events(self, events: list[KeyEvent]) -> None:
        accepted = self._accepted_signal_event_ids
        filtered_events = [
            event
            for event in events
            if not (
                event.type == pygame.KEYDOWN
                and (
                    event.key == pygame.K_p
                    or (
                        event.key in (pygame.K_z, pygame.K_x)
                        and accepted is not None
                        and id(event) not in accepted
                    )
                )
            )
        ]
        try:
            super()._handle_mode_events(filtered_events)
        finally:
            self._accepted_signal_event_ids = None

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
        wheel = self._tsxw_input
        sample = None if wheel is None else wheel.poll(events)
        combined_events = (
            events
            if sample is None
            else coalesce_command_events(
                self._tsxw_commands,
                events,
                sample,
                noa_key=self._research_config.noa_toggle_key,
            )
        )
        self._tsxw_mode_events = None if sample is None else combined_events
        driver_input = super()._collect_driver_input(combined_events)
        throttle_override = brake_override = steering_override = False
        if sample is not None:
            keys = pygame.key.get_pressed()
            throttle_override = bool(keys[pygame.K_w])
            brake_override = bool(keys[pygame.K_s])
            steering_override = bool(keys[pygame.K_a] or keys[pygame.K_d])
            driver_input = merge_driver_input(
                driver_input,
                sample,
                throttle_override=throttle_override,
                brake_override=brake_override,
                steering_override=steering_override,
            )
        has_indicator_event = any(
            event.type in (pygame.KEYDOWN, pygame.KEYUP)
            and event.key in (pygame.K_z, pygame.K_x)
            for event in combined_events
        )
        lights = self._hero.get_light_state() if has_indicator_event else None
        lane_change_direction = None
        self._accepted_signal_event_ids = frozenset()
        if lights is not None:
            accepted_signal = self._lane_change_input.accept(combined_events, lights)
            lane_change_direction = accepted_signal.direction
            self._accepted_signal_event_ids = accepted_signal.event_ids
        toggle_requested = any(
            event.type == pygame.KEYDOWN
            and event.key == self._research_config.noa_toggle_key
            for event in combined_events
        )
        active_indicator_keys: frozenset[int] = frozenset()
        if self._exit_view.indicator_confirmation:
            if lights is None:
                lights = self._hero.get_light_state()
            active_indicator_keys = frozenset(
                key
                for key, indicator in (
                    (pygame.K_x, carla.VehicleLightState.RightBlinker),
                )
                if lights & indicator
            )
        confirm_requested, reject_requested = self._exit_view.requests(
            combined_events,
            active_indicator_keys,
        )
        if (
            self._exit_view.indicator_confirmation
            and lane_change_direction is not LaneChangeDirection.RIGHT
        ):
            confirm_requested = False
        mode_source = self._research_config.automation_state_source
        if (
            lane_change_direction is not None
            and mode_source is not None
            and mode_source().control_mode is DrivingControlMode.NOA_ACTIVE
            and not (
                driver_input.brake > 0.0
                or driver_input.steering_engaged
                or toggle_requested
            )
        ):
            self._handle_mode_events(
                [
                    event
                    for event in combined_events
                    if id(event) in self._accepted_signal_event_ids
                ]
            )
            self._accepted_signal_event_ids = frozenset()
        collected = replace(
            driver_input,
            activation_requested=toggle_requested,
            deactivation_requested=toggle_requested,
            exit_confirm_requested=confirm_requested,
            exit_reject_requested=reject_requested,
            lane_change_direction=lane_change_direction,
        )
        sink = self._research_config.tsxw_event_sink
        if sample is not None and sink is not None:
            sink(
                TsxwLogEvent(
                    "tsxw_input_sample",
                    json.dumps(
                        {
                            "raw": {
                                "steering": sample.raw_steering,
                                "brake": sample.raw_brake,
                                "throttle": sample.raw_throttle,
                            },
                            "normalized": {
                                "steering": sample.steering,
                                "brake": sample.brake,
                                "throttle": sample.throttle,
                            },
                            "effective": {
                                "steering": collected.steering,
                                "brake": collected.brake,
                                "throttle": collected.throttle,
                                "hand_brake": collected.hand_brake,
                            },
                            "keyboard_override": {
                                "steering": steering_override,
                                "brake": brake_override,
                                "throttle": throttle_override,
                            },
                            "window_input_focus": pygame.key.get_focused(),
                            "edges": {
                                "left": sample.left_pressed,
                                "right": sample.right_pressed,
                                "noa": sample.noa_pressed,
                                "reject": sample.reject_pressed,
                            },
                        },
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
        return collected


class ResearchLiveDriverView(ResearchDriverView):
    def _apply_manual_control(self) -> None:
        if self._automation_owns_control():
            return
        limits = self._research_config.manual_control_limits
        if limits is None:
            DriverView._apply_manual_control(self)
            return
        if self.driving_mode is not DrivingMode.MANUAL:
            return
        driver_input = self._pending_driver_input or self._collect_driver_input(())
        self._pending_driver_input = None
        brake = min(driver_input.brake, limits.max_brake)
        throttle_suppressed = brake > 0.0
        control = carla.VehicleControl(
            throttle=(
                0.0
                if throttle_suppressed
                else min(driver_input.throttle, limits.max_throttle)
            ),
            brake=brake,
            steer=min(
                max(driver_input.steering, -limits.max_steering),
                limits.max_steering,
            ),
            hand_brake=driver_input.hand_brake,
        )
        self._send_control(control)
        sink = self._research_config.tsxw_event_sink
        if self._tsxw_input is not None and sink is not None:
            sink(
                TsxwLogEvent(
                    "tsxw_manual_control",
                    json.dumps(
                        {
                            "effective": {
                                "steering": driver_input.steering,
                                "brake": driver_input.brake,
                                "throttle": driver_input.throttle,
                                "hand_brake": driver_input.hand_brake,
                            },
                            "sent": {
                                "steering": control.steer,
                                "brake": control.brake,
                                "throttle": control.throttle,
                                "hand_brake": control.hand_brake,
                                "gear": control.gear,
                                "reverse": control.reverse,
                                "manual_gear_shift": control.manual_gear_shift,
                                "transport": (
                                    "CHECKED_BATCH_SYNC"
                                    if self._research_config.control_sender is not None
                                    else "VEHICLE_APPLY_CONTROL"
                                ),
                            },
                            "throttle_suppression": (
                                "POSITIVE_BRAKE" if throttle_suppressed else None
                            ),
                        },
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
