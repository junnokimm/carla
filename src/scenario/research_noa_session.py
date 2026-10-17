from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace

import carla

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction import (
    AutomationInteractionController,
    AutomationInteractionEvent,
)
from src.experiment.automation_interaction_types import AutomationInitializationError
from src.experiment.exit_assistance import (
    ExitAssistanceCoordinator,
    ExitTerminationReason,
)
from src.experiment.noa_runtime import CarlaNoARuntimeWaypoint, NoARuntimeBundle
from src.experiment.timestamp import TimestampEnvelope
from src.p6.runtime import P6Runtime
from src.p8.native_tm_diagnostics import NativeTrafficManagerDiagnosticsCollector
from src.scenario.active_scene import ActiveDriveScene
from src.scenario.driver_view import RuntimeFrameContinuation
from src.scenario.research_camera_metadata import ResearchCameraRunMeasurements
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitViewBinding,
)
from src.scenario.research_lane_change import ResearchLaneChangeInteractionObserver
from src.scenario.research_noa_cleanup import destroy_owned_hero
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_control_delivery import ControlSender
from src.scenario.research_noa_diagnostics import ResearchWorldTiming
from src.scenario.research_noa_lifecycle import (
    preserve_primary_error,
    retain_first_error,
    run_with_persistence,
)
from src.scenario.research_noa_observation import current_lane_id
from src.scenario.research_noa_persistence import (
    PersistedInteractionObserver,
    ResearchNoAPersistence,
)
from src.scenario.research_noa_report import (
    ResearchSmokeReport,
    build_research_smoke_report,
)
from src.scenario.research_noa_shutdown import (
    ShutdownBrakeCommandOutcome,
    ShutdownBrakeOutcome,
    ShutdownExceptionDetails,
    host_timestamp,
)
from src.scenario.research_noa_smoke import (
    ResearchDryRunScheduler,
    ResearchLiveScheduler,
    ResearchNoAActualSpeedSafetyError,
    observe_live_smoke_speed_kmh,
)
from src.scenario.research_noa_speed_policy import EffectiveResearchSpeedPolicy
from src.scenario.research_noa_transmission import (
    MonotonicClock,
    TransmissionPrimeContext,
    prime_live_transmission,
)
from src.scenario.research_noa_types import (
    ResearchNoAVehicle,
    ResearchNoAViewer,
    ResearchNoAWorld,
    ResearchNoAWorldSnapshot,
)
from src.scenario.research_performance_summary import build_performance_summary


class ResearchNoAPreflightError(RuntimeError):
    pass


class ResearchNoADryRunActivationError(RuntimeError):
    pass


class ResearchNoASession:
    def __init__(
        self,
        *,
        world: ResearchNoAWorld,
        hero: ResearchNoAVehicle,
        viewer: ResearchNoAViewer,
        bundle: NoARuntimeBundle,
        config: ResearchNoARunConfig,
        spawn_transform: carla.Transform,
        spawn_waypoint: CarlaNoARuntimeWaypoint | None,
        dry_scheduler: ResearchDryRunScheduler,
        live_scheduler: ResearchLiveScheduler | None,
        monotonic_clock: MonotonicClock,
        interaction: AutomationInteractionController
        | PersistedInteractionObserver
        | ResearchLaneChangeInteractionObserver
        | ResearchExitInteractionObserver
        | None = None,
        interaction_events: list[AutomationInteractionEvent] | None = None,
        persistence: ResearchNoAPersistence | None = None,
        exit_assistance: ExitAssistanceCoordinator | None = None,
        exit_view_binding: ResearchExitViewBinding | None = None,
        p6: P6Runtime | None = None,
        speed_policy: EffectiveResearchSpeedPolicy | None,
        control_sender: ControlSender | None = None,
        native_tm_collector: NativeTrafficManagerDiagnosticsCollector | None = None,
    ) -> None:
        self.world = world
        self.hero = hero
        self.viewer = viewer
        self.bundle = bundle
        self.config = config
        self.spawn_transform = spawn_transform
        self.spawn_waypoint = spawn_waypoint
        self.dry_scheduler = dry_scheduler
        self.live_scheduler = live_scheduler
        self.monotonic_clock = monotonic_clock
        self.interaction = interaction
        self.interaction_events = (
            interaction_events if interaction_events is not None else []
        )
        self.persistence = persistence
        self.exit_assistance = exit_assistance
        self.exit_view_binding = exit_view_binding
        self.p6 = p6
        self.speed_policy = speed_policy
        self.control_sender = control_sender
        self.native_tm_collector = native_tm_collector
        self._closed = False
        self._live_shutdown_completed = False
        self._live_shutdown_timestamp: TimestampEnvelope | None = None
        self.shutdown_brake_outcome = ShutdownBrakeOutcome.not_attempted()
        self.active_scene_factory: (
            Callable[[ResearchNoASession, float], ActiveDriveScene] | None
        ) = None

    def request_control_mode(self, mode: DrivingControlMode) -> None:
        if (
            self.config.mode is ResearchNoARunMode.DRY_RUN
            and mode is DrivingControlMode.NOA_ACTIVE
        ):
            raise ResearchNoADryRunActivationError
        if (
            self.config.mode is ResearchNoARunMode.LIVE_SMOKE
            and mode is DrivingControlMode.MANUAL
            and (
                self.bundle.automation_runtime.state.control_mode
                is DrivingControlMode.NOA_ACTIVE
                or self.bundle.traffic_manager_backend is not None
            )
        ):
            self._deactivate_live()
            return
        self.bundle.automation_runtime.request_control_mode(mode)

    def run(
        self,
        before_live_shutdown: Callable[[ResearchNoASession], None] | None = None,
        *,
        continuation_factory: Callable[
            [ResearchNoASession], RuntimeFrameContinuation | None
        ]
        | None = None,
    ) -> ResearchSmokeReport | None:
        if self.persistence is None:
            return self._run(before_live_shutdown, continuation_factory)
        return run_with_persistence(
            lambda: self._run(before_live_shutdown, continuation_factory),
            self.persistence,
        )

    def _run(
        self,
        before_live_shutdown: Callable[[ResearchNoASession], None] | None,
        continuation_factory: Callable[
            [ResearchNoASession], RuntimeFrameContinuation | None
        ]
        | None,
    ) -> ResearchSmokeReport | None:
        if self.config.mode is ResearchNoARunMode.DRY_RUN:
            if self.persistence is not None:
                self.persistence.start_stage()
            if not self.config.camera_diagnostics:
                self.viewer.run(self.config.duration, scheduler=self.dry_scheduler)
                return None
            initial_snapshot = self.world.get_snapshot()
            started_at = self.monotonic_clock()
            self.viewer.run(self.config.duration, scheduler=self.dry_scheduler)
            elapsed_seconds = self.monotonic_clock() - started_at
            final_snapshot = self.world.get_snapshot()
            camera_diagnostics_json = self.viewer.camera_diagnostics_json(
                ResearchCameraRunMeasurements(
                    initial_world_frame=int(initial_snapshot.frame),
                    final_world_frame=int(final_snapshot.frame),
                    initial_simulation_seconds=float(
                        initial_snapshot.timestamp.elapsed_seconds
                    ),
                    final_simulation_seconds=float(
                        final_snapshot.timestamp.elapsed_seconds
                    ),
                    host_elapsed_seconds=elapsed_seconds,
                    loop_count=self.dry_scheduler.update_count,
                )
            )
            if camera_diagnostics_json is not None:
                print(f"camera_diagnostics_json={camera_diagnostics_json}")
            return None

        if self.live_scheduler is None or self.spawn_waypoint is None:
            raise ResearchNoAPreflightError("live smoke preflight is incomplete")
        speed_policy = self.speed_policy
        if speed_policy is None and self.config.traffic_manager_port is None:
            raise ResearchNoAPreflightError("live smoke speed policy is missing")

        if (spawn_index := self.config.spawn_index) is None:
            raise ResearchNoAPreflightError("live smoke spawn index is missing")
        if self.persistence is not None and speed_policy is not None:
            self.persistence.record_speed_policy(
                speed_policy,
                self.config.control_config,
            )
        if (
            self.persistence is not None
            and self.config.traffic_manager_port is not None
        ):
            self.persistence.record_control_authority(
                self.config.traffic_manager_port,
                self.bundle.automation_runtime.state,
            )
        try:
            initial_speed_kmh = self._observe_live_speed("INITIAL")
        except ResearchNoAActualSpeedSafetyError as error:
            self._record_speed_violation(error, "NO_CONTROL_PRECONDITION")
            raise
        brake = self.config.control_config.longitudinal.max_brake
        context = TransmissionPrimeContext(
            brake,
            initial_speed_kmh,
            self.monotonic_clock,
            preparation_timeout_seconds=self.config.timeout,
            control_sender=self.control_sender,
        )
        prime = prime_live_transmission(self.hero, self.world, context)
        started_at = self.monotonic_clock()
        user_exited = False
        training_completed_early = False
        active_drive_seconds = 0.0
        viewer_started = False
        final_snapshot: ResearchNoAWorldSnapshot | None = None
        camera_diagnostics_json: str | None = None
        deferred_performance_error: BaseException | None = None
        performance_capture_error: BaseException | None = None
        initial_snapshot: ResearchNoAWorldSnapshot | None = None
        m2_boundary_captured = False
        scheduler_updates_at_boundary = 0
        driver_loops_at_boundary = 0
        commands_at_boundary = self.live_scheduler.commands
        maximum_manual_speed_at_boundary = self.live_scheduler.maximum_manual_speed_kmh
        final_speed_kmh: float | None = None
        active_scene = None
        try:
            if self.interaction is None:
                self.request_control_mode(DrivingControlMode.NOA_ACTIVE)
            else:
                interaction_config = self.config.automation_interaction
                if interaction_config is None:
                    raise ResearchNoAPreflightError("automation interaction is missing")
                self.interaction.initialize(
                    interaction_config.module,
                    interaction_config.condition,
                    lane_change_in_progress=(
                        interaction_config.initial_lane_change_in_progress
                    ),
                    stage=interaction_config.initial_stage,
                )
                failure = self.interaction.initialization_failure
                if failure is not None:
                    raise AutomationInitializationError(
                        interaction_config.module,
                        interaction_config.condition,
                        failure,
                    )
                if (
                    self.bundle.automation_runtime.state.control_mode
                    is DrivingControlMode.NOA_ACTIVE
                ):
                    self._observe_live_speed("POST_INITIALIZATION")
            if self.p6 is not None:
                self.p6.start()
            if self.persistence is not None:
                self.persistence.start_stage()
            if self.exit_assistance is not None:
                if self.persistence is None:
                    raise ResearchNoAPreflightError("exit timing requires persistence")
                self.exit_assistance.start_active_drive(
                    self.persistence.source.get_host_timestamp()
                )
            initial_snapshot = self.world.get_snapshot()
            active_drive_started_at = self.monotonic_clock()
            if self.active_scene_factory is not None:
                active_scene = self.active_scene_factory(self, active_drive_started_at)
            self.live_scheduler.diagnostics.begin_operation_measurement_window()

            def create_continuation() -> RuntimeFrameContinuation | None:
                nonlocal active_drive_seconds
                nonlocal driver_loops_at_boundary
                nonlocal final_snapshot
                nonlocal m2_boundary_captured
                nonlocal scheduler_updates_at_boundary
                nonlocal commands_at_boundary
                nonlocal final_speed_kmh
                nonlocal maximum_manual_speed_at_boundary
                self.live_scheduler.diagnostics.freeze_measurement_window()
                self.viewer.freeze_performance_measurement_window()
                m2_boundary_captured = True
                active_drive_seconds = self.monotonic_clock() - active_drive_started_at
                final_snapshot = self.world.get_snapshot()
                scheduler_updates_at_boundary = self.live_scheduler.update_count
                commands_at_boundary = self.live_scheduler.commands
                maximum_manual_speed_at_boundary = (
                    self.live_scheduler.maximum_manual_speed_kmh
                )
                final_speed_kmh = self._observe_live_speed("M2_BOUNDARY")
                driver_loops_at_boundary = (
                    self.live_scheduler.diagnostics.completed_driver_loop_count
                )
                if self.persistence is not None:
                    self.persistence.flush_events()
                    self.persistence.end_stage("NORMAL_COMPLETION")
                if continuation_factory is None:
                    return None
                return continuation_factory(self)

            try:
                viewer_started = True
                if active_scene is not None:
                    user_exited = self.viewer.run(
                        self.config.duration,
                        scheduler=self.live_scheduler,
                        diagnostics=self.live_scheduler.diagnostics,
                        input_observer=self.interaction,
                        active_scene=active_scene,
                    )
                elif continuation_factory is None:
                    user_exited = self.viewer.run(
                        self.config.duration,
                        scheduler=self.live_scheduler,
                        diagnostics=self.live_scheduler.diagnostics,
                        input_observer=self.interaction,
                    )
                else:
                    user_exited = self.viewer.run(
                        self.config.duration,
                        scheduler=self.live_scheduler,
                        diagnostics=self.live_scheduler.diagnostics,
                        input_observer=self.interaction,
                        continuation_factory=create_continuation,
                    )
            finally:
                if not m2_boundary_captured:
                    self.live_scheduler.diagnostics.end_operation_measurement_window()
                    active_drive_seconds = (
                        self.monotonic_clock() - active_drive_started_at
                    )
                    scheduler_updates_at_boundary = self.live_scheduler.update_count
                    commands_at_boundary = self.live_scheduler.commands
                    maximum_manual_speed_at_boundary = (
                        self.live_scheduler.maximum_manual_speed_kmh
                    )
                    driver_loops_at_boundary = (
                        self.live_scheduler.diagnostics.completed_driver_loop_count
                    )
                    try:
                        final_snapshot = self.world.get_snapshot()
                    except (OSError, RuntimeError, TypeError, ValueError) as error:
                        performance_capture_error = error
            training_completed_early = self.viewer.training_completed_early
            if final_speed_kmh is None:
                final_speed_kmh = self._observe_live_speed("FINAL")
            if (
                before_live_shutdown is not None
                and not user_exited
                and performance_capture_error is None
            ):
                if self.persistence is not None:
                    self.persistence.flush_events()
                before_live_shutdown(self)
        finally:
            elapsed_seconds = self.monotonic_clock() - started_at
            trigger_state = self.bundle.automation_runtime.state
            was_active = trigger_state.control_mode is DrivingControlMode.NOA_ACTIVE
            run_error = sys.exception()
            primary_error = run_error
            if active_scene is not None:
                primary_error = retain_first_error(
                    lambda: active_scene.finalize(
                        "ERROR"
                        if run_error is not None
                        else "USER_EXIT"
                        if user_exited
                        else "M2_END",
                        run_error,
                    ),
                    primary_error,
                )
            shutdown_timestamps: list[TimestampEnvelope | None] = []
            try:
                shutdown_timestamps.append(self._shutdown_live())
            except (
                KeyboardInterrupt,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    primary_error.add_note(
                        "Traffic Manager shutdown cleanup failure: "
                        f"{type(cleanup_error).__name__}:{cleanup_error}"
                    )
            shutdown_timestamp = (
                None if not shutdown_timestamps else shutdown_timestamps[0]
            )
            if (
                isinstance(
                    self.interaction,
                    (
                        ResearchLaneChangeInteractionObserver,
                        ResearchExitInteractionObserver,
                    ),
                )
                and self.persistence is not None
            ):
                primary_error = retain_first_error(
                    lambda: self.interaction.finish_lane_change_observation(
                        "ERROR"
                        if primary_error is not None
                        else "USER_EXIT"
                        if user_exited
                        else "NORMAL_STOP",
                        shutdown_timestamp
                        if shutdown_timestamp is not None
                        else self.persistence.source.get_host_timestamp(),
                    ),
                    primary_error,
                )
            if self.persistence is not None:
                primary_error = retain_first_error(
                    self.persistence.flush_events, primary_error
                )
                if was_active:
                    primary_error = retain_first_error(
                        lambda: self.persistence.record_shutdown(
                            "USER_EXIT" if user_exited else "SAFETY_SHUTDOWN",
                            self.bundle.automation_runtime.state,
                            timestamp=shutdown_timestamp,
                        ),
                        primary_error,
                    )
            if self.exit_assistance is not None and self.persistence is not None:
                exit_reason = (
                    ExitTerminationReason.ERROR
                    if primary_error is not None
                    else ExitTerminationReason.USER_EXIT
                    if user_exited
                    else ExitTerminationReason.NORMAL
                    if training_completed_early
                    else ExitTerminationReason.TIME_CAP
                )
                primary_error = retain_first_error(
                    lambda: self.exit_assistance.finish(
                        exit_reason,
                        self.persistence.source.get_host_timestamp(),
                    ),
                    primary_error,
                )
            if self.p6 is not None:
                primary_error = retain_first_error(self.p6.stop, primary_error)
            if viewer_started:
                performance_error = primary_error or performance_capture_error
                if performance_capture_error is not None:
                    if primary_error is None:
                        deferred_performance_error = performance_capture_error
                    else:
                        primary_error.add_note(
                            "Performance final snapshot failure: "
                            f"{type(performance_capture_error).__name__}:"
                            f"{performance_capture_error}"
                        )
                measurements = (
                    None
                    if initial_snapshot is None or final_snapshot is None
                    else ResearchCameraRunMeasurements(
                        initial_world_frame=int(initial_snapshot.frame),
                        final_world_frame=int(final_snapshot.frame),
                        initial_simulation_seconds=float(
                            initial_snapshot.timestamp.elapsed_seconds
                        ),
                        final_simulation_seconds=float(
                            final_snapshot.timestamp.elapsed_seconds
                        ),
                        host_elapsed_seconds=active_drive_seconds,
                        loop_count=driver_loops_at_boundary,
                    )
                )
                try:
                    camera_diagnostics_json = (
                        self.viewer.camera_diagnostics_json(measurements)
                        if self.config.camera_diagnostics
                        else None
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as error:
                    if performance_error is None:
                        performance_error = error
                        deferred_performance_error = error
                    else:
                        performance_error.add_note(
                            "Camera performance summary failure: "
                            f"{type(error).__name__}:{error}"
                        )
                if self.persistence is not None:
                    try:
                        performance_json = build_performance_summary(
                            diagnostics=self.live_scheduler.diagnostics,
                            world=(
                                None
                                if initial_snapshot is None or final_snapshot is None
                                else ResearchWorldTiming.from_snapshots(
                                    initial_snapshot, final_snapshot
                                )
                            ),
                            wall_elapsed_seconds=active_drive_seconds,
                            scheduler_updates=scheduler_updates_at_boundary,
                            camera_diagnostics_json=camera_diagnostics_json,
                            error=performance_error,
                        )
                        completed_timestamp = (
                            self.persistence.source.get_host_timestamp()
                        )
                        self.persistence.record_phase2_event(
                            "performance_summary",
                            performance_json,
                            completed_timestamp,
                        )
                    except (OSError, RuntimeError, TypeError, ValueError) as error:
                        if performance_error is None:
                            raise
                        performance_error.add_note(
                            "Performance summary persistence failure: "
                            f"{type(error).__name__}:{error}"
                        )
            if self.persistence is not None:
                primary_error = retain_first_error(
                    self.persistence.flush_events,
                    primary_error,
                )
            if isinstance(primary_error, ResearchNoAActualSpeedSafetyError):
                self._record_speed_violation(
                    primary_error,
                    "BOUNDED_SHUTDOWN_REQUESTED",
                    state=trigger_state,
                )
            if deferred_performance_error is not None and primary_error is None:
                raise deferred_performance_error
            if run_error is None and primary_error is not None:
                raise primary_error
        if initial_snapshot is None or final_snapshot is None:
            raise ResearchNoAPreflightError("performance final snapshot is missing")
        if final_speed_kmh is None:
            raise ResearchNoAPreflightError("performance final speed is missing")
        diagnostics = self.live_scheduler.diagnostics.summarize(
            ResearchWorldTiming.from_snapshots(initial_snapshot, final_snapshot),
            active_drive_seconds,
            scheduler_updates_at_boundary,
        )

        return build_research_smoke_report(
            config=self.config,
            world_map=self.world.get_map(),
            hero=self.hero,
            spawn_transform=self.spawn_transform,
            spawn_index=spawn_index,
            initial_lane_id=int(self.spawn_waypoint.lane_id),
            final_lane_id=current_lane_id(self.world, self.hero),
            initial_speed_kmh=initial_speed_kmh,
            final_speed_kmh=final_speed_kmh,
            transmission_prime=prime,
            elapsed_seconds=elapsed_seconds,
            active_drive_seconds=active_drive_seconds,
            diagnostics=diagnostics,
            user_exited=user_exited,
            scheduler_updates=scheduler_updates_at_boundary,
            commands=commands_at_boundary,
            maximum_manual_speed_kmh=maximum_manual_speed_at_boundary,
            final_control_mode=self.live_scheduler.runtime.state.control_mode,
            camera_diagnostics_json=camera_diagnostics_json,
            training_completed_early=training_completed_early,
            applied_target_speed_kmh=(
                None
                if self.bundle.custom_control_backend is None
                else self.bundle.custom_control_backend.target_speed_kmh
            ),
        )

    def _deactivate_live(self) -> TimestampEnvelope | None:
        shutdown_timestamp: TimestampEnvelope | None = None
        phase_exception: ShutdownExceptionDetails | None = None
        was_active = (
            self.bundle.automation_runtime.state.control_mode
            is DrivingControlMode.NOA_ACTIVE
        )
        try:
            self.bundle.automation_runtime.request_control_mode(
                DrivingControlMode.MANUAL
            )
            if not was_active and self.bundle.traffic_manager_backend is not None:
                self.bundle.traffic_manager_backend.ensure_manual_control()
            shutdown_timestamp = (
                self.persistence.source.get_host_timestamp()
                if self.persistence is not None
                else None
            )
            return shutdown_timestamp
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            phase_exception = ShutdownExceptionDetails.from_exception(error)
            raise
        finally:
            brake_control = carla.VehicleControl(
                throttle=0.0,
                brake=self.config.control_config.longitudinal.max_brake,
                steer=0.0,
            )
            if self.live_scheduler is not None:
                self.live_scheduler.diagnostics.set_control_mode(
                    self.bundle.automation_runtime.state.control_mode
                )
            try:
                (self.control_sender or self.hero.apply_control)(brake_control)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                self.shutdown_brake_outcome = ShutdownBrakeOutcome(
                    ShutdownBrakeCommandOutcome.RAISED,
                    requested_throttle=0.0,
                    requested_brake=float(brake_control.brake),
                    requested_steer=0.0,
                    command_started_host=host_timestamp(shutdown_timestamp),
                    command_exception=ShutdownExceptionDetails.from_exception(error),
                    shutdown_phase_exception=phase_exception,
                )
                if phase_exception is None:
                    raise
            else:
                self.shutdown_brake_outcome = ShutdownBrakeOutcome(
                    ShutdownBrakeCommandOutcome.RETURNED,
                    requested_throttle=0.0,
                    requested_brake=float(brake_control.brake),
                    requested_steer=0.0,
                    command_started_host=host_timestamp(shutdown_timestamp),
                    shutdown_phase_exception=phase_exception,
                )
            finally:
                if self.persistence is not None:
                    try:
                        completed = self.persistence.source.get_host_timestamp()
                    except (OSError, RuntimeError, TypeError, ValueError) as error:
                        self.shutdown_brake_outcome = replace(
                            self.shutdown_brake_outcome,
                            completion_timestamp_exception=(
                                ShutdownExceptionDetails.from_exception(error)
                            ),
                        )
                    else:
                        self.shutdown_brake_outcome = replace(
                            self.shutdown_brake_outcome,
                            command_completed_host=completed.host,
                        )

    def _observe_live_speed(self, source: str) -> float:
        if self.config.traffic_manager_port is not None:
            return observe_live_smoke_speed_kmh(
                self.hero,
                None,
                source=source,
            )
        if self.speed_policy is None:
            raise ResearchNoAPreflightError("live smoke speed policy is missing")
        return observe_live_smoke_speed_kmh(
            self.hero,
            self.speed_policy.guard_for_mode(
                self.bundle.automation_runtime.state.control_mode
            ),
            source=source,
        )

    def _record_speed_violation(
        self,
        error: ResearchNoAActualSpeedSafetyError,
        commanded_response: str,
        *,
        state: AutomationState | None = None,
    ) -> None:
        if self.persistence is None:
            return
        speed_policy = self.speed_policy
        if speed_policy is None:
            self.persistence.record_nonfinite_speed_violation(
                self.bundle.automation_runtime.state if state is None else state,
                source=error.source,
                commanded_response=commanded_response,
            )
            return
        trigger_state = self.bundle.automation_runtime.state if state is None else state

        def publish_shutdown_brake(outcome: ShutdownBrakeOutcome) -> None:
            self.shutdown_brake_outcome = outcome

        def record_violation() -> None:
            self.persistence.record_speed_limit_violation(
                speed_policy,
                self.config.control_config,
                trigger_state,
                triggering_speed_kmh=error.actual_speed_kmh,
                source=error.source,
                commanded_response=commanded_response,
                shutdown_brake=self.shutdown_brake_outcome,
                publish_shutdown_brake=publish_shutdown_brake,
            )

        preserve_primary_error(record_violation, error)

    def _shutdown_live(self) -> TimestampEnvelope | None:
        if self._live_shutdown_completed:
            return self._live_shutdown_timestamp
        shutdown_timestamp = self._deactivate_live()
        self._live_shutdown_timestamp = shutdown_timestamp
        self._live_shutdown_completed = True
        return shutdown_timestamp

    def close(self) -> None:
        if self._closed:
            return
        inherited_primary = sys.exception()
        primary_error = inherited_primary
        if not self._live_shutdown_completed:
            primary_error = retain_first_error(
                lambda: self.request_control_mode(DrivingControlMode.MANUAL),
                primary_error,
            )
        if self.native_tm_collector is not None:
            if isinstance(primary_error, KeyboardInterrupt):
                self.native_tm_collector.mark_interrupted(primary_error)
            primary_error = retain_first_error(
                self.native_tm_collector.close,
                primary_error,
            )
        if self.p6 is not None:
            primary_error = retain_first_error(self.p6.close, primary_error)
        primary_error = retain_first_error(self.viewer.close, primary_error)
        primary_error = retain_first_error(
            lambda: destroy_owned_hero(self.hero),
            primary_error,
        )
        self._closed = True
        if inherited_primary is None and primary_error is not None:
            raise primary_error
