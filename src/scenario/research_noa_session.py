from __future__ import annotations

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.noa_runtime import CarlaNoARuntimeWaypoint, NoARuntimeBundle
from src.scenario.research_noa_cleanup import destroy_owned_hero
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_diagnostics import ResearchWorldTiming
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
        if self.config.mode is ResearchNoARunMode.DRY_RUN:
            self.viewer.run(self.config.duration, scheduler=self.dry_scheduler)
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
        self.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        started_at = self.monotonic_clock()
        try:
            user_exited = self.viewer.run(
                self.config.duration,
                scheduler=self.live_scheduler,
                diagnostics=self.live_scheduler.diagnostics,
            )
            final_speed_kmh = observe_live_smoke_speed_kmh(self.hero)
        finally:
            elapsed_seconds = self.monotonic_clock() - started_at
            self.request_control_mode(DrivingControlMode.MANUAL)
        final_snapshot = self.world.get_snapshot()
        diagnostics = self.live_scheduler.diagnostics.summarize(
            ResearchWorldTiming.from_snapshots(initial_snapshot, final_snapshot),
            elapsed_seconds,
            self.live_scheduler.update_count,
        )

        return build_research_smoke_report(
            config=self.config,
            world_map=self.world.get_map(),
            hero=self.hero,
            spawn_transform=self.spawn_transform,
            spawn_index=spawn_index,
            initial_lane_id=int(self.spawn_waypoint.lane_id),
            final_lane_id=self._current_lane_id(),
            initial_speed_kmh=initial_speed_kmh,
            final_speed_kmh=final_speed_kmh,
            transmission_prime=prime,
            elapsed_seconds=elapsed_seconds,
            diagnostics=diagnostics,
            user_exited=user_exited,
            scheduler=self.live_scheduler,
        )

    def _deactivate_live(self) -> None:
        try:
            self.bundle.automation_runtime.request_control_mode(
                DrivingControlMode.MANUAL
            )
        finally:
            self.hero.apply_control(
                carla.VehicleControl(
                    throttle=0.0,
                    brake=self.config.control_config.longitudinal.max_brake,
                    steer=0.0,
                )
            )

    def _current_lane_id(self) -> int | None:
        transform = self.hero.get_transform()
        waypoint = self.world.get_map().get_waypoint(
            transform.location,
            project_to_road=False,
            lane_type=carla.LaneType.Driving,
        )
        return None if waypoint is None else int(waypoint.lane_id)

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
