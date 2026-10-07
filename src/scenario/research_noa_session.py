from __future__ import annotations

import sys

import carla

from src.experiment.automation import DrivingControlMode
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
from src.scenario.research_camera_metadata import ResearchCameraRunMeasurements
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitViewBinding,
)
from src.scenario.research_noa_cleanup import destroy_owned_hero
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_diagnostics import ResearchWorldTiming
from src.scenario.research_noa_lifecycle import (
    preserve_primary_error,
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
from src.scenario.research_noa_smoke import (
    ResearchAutomationScheduler,
    ResearchDryRunScheduler,
    observe_live_smoke_speed_kmh,
)
from src.scenario.research_noa_transmission import (
    MonotonicClock,
    TransmissionPrimeContext,
    prime_live_transmission,
)
from src.scenario.research_noa_types import (
    ResearchNoAVehicle,
    ResearchNoAViewer,
    ResearchNoAWorld,
)


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
        live_scheduler: ResearchAutomationScheduler | None,
        monotonic_clock: MonotonicClock,
        interaction: AutomationInteractionController
        | PersistedInteractionObserver
        | ResearchExitInteractionObserver
        | None = None,
        interaction_events: list[AutomationInteractionEvent] | None = None,
        persistence: ResearchNoAPersistence | None = None,
        exit_assistance: ExitAssistanceCoordinator | None = None,
        exit_view_binding: ResearchExitViewBinding | None = None,
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
        self._closed = False

    def request_control_mode(self, mode: DrivingControlMode) -> None:
        if (
            self.config.mode is ResearchNoARunMode.DRY_RUN
            and mode is DrivingControlMode.NOA_ACTIVE
        ):
            raise ResearchNoADryRunActivationError
        if (
            self.config.mode is ResearchNoARunMode.LIVE_SMOKE
            and self.bundle.automation_runtime.state.control_mode
            is DrivingControlMode.NOA_ACTIVE
            and mode is DrivingControlMode.MANUAL
        ):
            self._deactivate_live()
            return
        self.bundle.automation_runtime.request_control_mode(mode)

    def run(self) -> ResearchSmokeReport | None:
        if self.persistence is None:
            return self._run()
        return run_with_persistence(self._run, self.persistence)

    def _run(self) -> ResearchSmokeReport | None:
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

        if (spawn_index := self.config.spawn_index) is None:
            raise ResearchNoAPreflightError("live smoke spawn index is missing")
        initial_speed_kmh = observe_live_smoke_speed_kmh(self.hero)
        brake = self.config.control_config.longitudinal.max_brake
        context = TransmissionPrimeContext(
            brake, initial_speed_kmh, self.monotonic_clock
        )
        prime = prime_live_transmission(self.hero, self.world, context)
        initial_snapshot = self.world.get_snapshot()
        started_at = self.monotonic_clock()
        user_exited = False
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
            if self.persistence is not None:
                self.persistence.start_stage()
            user_exited = self.viewer.run(
                self.config.duration,
                scheduler=self.live_scheduler,
                diagnostics=self.live_scheduler.diagnostics,
                input_observer=self.interaction,
            )
            final_speed_kmh = observe_live_smoke_speed_kmh(self.hero)
        finally:
            elapsed_seconds = self.monotonic_clock() - started_at
            was_active = (
                self.bundle.automation_runtime.state.control_mode
                is DrivingControlMode.NOA_ACTIVE
            )
            primary_error = sys.exception()
            if self.exit_assistance is not None and self.persistence is not None:
                exit_reason = (
                    ExitTerminationReason.ERROR
                    if primary_error is not None
                    else ExitTerminationReason.USER_EXIT
                    if user_exited
                    else ExitTerminationReason.TIME_CAP
                )
                self.exit_assistance.finish(
                    exit_reason,
                    self.persistence.source.get_host_timestamp(),
                )
            shutdown_timestamp = self._shutdown_live()
            if self.persistence is not None:
                preserve_primary_error(
                    self.persistence.flush_events,
                    primary_error,
                )
            if was_active and self.persistence is not None:
                preserve_primary_error(
                    lambda: self.persistence.record_shutdown(
                        "USER_EXIT" if user_exited else "SAFETY_SHUTDOWN",
                        self.bundle.automation_runtime.state,
                        timestamp=shutdown_timestamp,
                    ),
                    primary_error,
                )
        final_snapshot = self.world.get_snapshot()
        diagnostics = self.live_scheduler.diagnostics.summarize(
            ResearchWorldTiming.from_snapshots(initial_snapshot, final_snapshot),
            elapsed_seconds,
            self.live_scheduler.update_count,
        )
        camera_diagnostics_json = (
            self.viewer.camera_diagnostics_json(
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
                    loop_count=self.live_scheduler.update_count,
                )
            )
            if self.config.camera_diagnostics
            else None
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
            diagnostics=diagnostics,
            user_exited=user_exited,
            scheduler=self.live_scheduler,
            camera_diagnostics_json=camera_diagnostics_json,
        )

    def _deactivate_live(self) -> TimestampEnvelope | None:
        try:
            self.bundle.automation_runtime.request_control_mode(
                DrivingControlMode.MANUAL
            )
            return (
                self.persistence.source.get_host_timestamp()
                if self.persistence is not None else None
            )
        finally:
            self.hero.apply_control(
                carla.VehicleControl(
                    throttle=0.0,
                    brake=self.config.control_config.longitudinal.max_brake,
                    steer=0.0,
                )
            )

    def _shutdown_live(self) -> TimestampEnvelope | None:
        return self._deactivate_live()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.request_control_mode(DrivingControlMode.MANUAL)
        finally:
            try:
                self.viewer.close()
            finally:
                destroy_owned_hero(self.hero)
                self._closed = True
