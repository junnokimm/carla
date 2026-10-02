from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.noa_runtime import (
    CarlaNoARuntimeWaypoint,
    NoARuntimeBundle,
    build_noa_runtime,
)
from src.scenario.driver_view import DriverViewConfig
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_smoke import (
    ResearchAutomationScheduler,
    ResearchDryRunScheduler,
    ResearchSmokeReport,
    build_research_smoke_report,
    observe_live_smoke_speed_kmh,
)
from src.scenario.research_noa_types import (
    ResearchNoAClient,
    ResearchNoAMap,
    ResearchNoAVehicle,
    ResearchNoAViewer,
    ResearchNoAViewerFactory,
    ResearchNoAWorld,
)
from src.scenario.research_noa_view import ResearchDriverView, ResearchLiveDriverView
from src.vehicle.driving_mode import DrivingMode


class ResearchNoAPreflightError(RuntimeError):
    pass


class ResearchNoADryRunActivationError(RuntimeError):
    pass


class ResearchNoACleanupError(RuntimeError):
    pass


class ResearchNoASession:
    def __init__(
        self,
        *,
        world_map: ResearchNoAMap,
        hero: ResearchNoAVehicle,
        viewer: ResearchNoAViewer,
        bundle: NoARuntimeBundle,
        config: ResearchNoARunConfig,
        spawn_transform: carla.Transform,
        spawn_waypoint: CarlaNoARuntimeWaypoint | None,
        dry_scheduler: ResearchDryRunScheduler,
        live_scheduler: ResearchAutomationScheduler | None,
    ) -> None:
        self.world_map = world_map
        self.hero = hero
        self.viewer = viewer
        self.bundle = bundle
        self.config = config
        self.spawn_transform = spawn_transform
        self.spawn_waypoint = spawn_waypoint
        self.dry_scheduler = dry_scheduler
        self.live_scheduler = live_scheduler
        self._closed = False

    def request_control_mode(self, mode: DrivingControlMode) -> None:
        if (
            self.config.mode is ResearchNoARunMode.DRY_RUN
            and mode is DrivingControlMode.NOA_ACTIVE
        ):
            raise ResearchNoADryRunActivationError(
                "dry run cannot activate custom control"
            )
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

        spawn_index = self.config.spawn_index
        if spawn_index is None:
            raise ResearchNoAPreflightError("live smoke spawn index is missing")
        initial_lane_id = int(self.spawn_waypoint.lane_id)
        initial_speed_kmh = observe_live_smoke_speed_kmh(self.hero)
        self.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        started_at = monotonic()
        try:
            user_exited = self.viewer.run(
                self.config.duration,
                scheduler=self.live_scheduler,
            )
            final_speed_kmh = observe_live_smoke_speed_kmh(self.hero)
        finally:
            elapsed_seconds = monotonic() - started_at
            self.request_control_mode(DrivingControlMode.MANUAL)

        return build_research_smoke_report(
            config=self.config,
            world_map=self.world_map,
            hero=self.hero,
            spawn_transform=self.spawn_transform,
            spawn_index=spawn_index,
            initial_lane_id=initial_lane_id,
            final_lane_id=self._current_lane_id(),
            initial_speed_kmh=initial_speed_kmh,
            final_speed_kmh=final_speed_kmh,
            elapsed_seconds=elapsed_seconds,
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
        waypoint = self.world_map.get_waypoint(
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
                _destroy_owned_hero(self.hero)
                self._closed = True


class ResearchNoARunner:
    def __init__(
        self,
        config: ResearchNoARunConfig,
        *,
        client: ResearchNoAClient | None = None,
        viewer_factory: ResearchNoAViewerFactory | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._viewer_factory = viewer_factory

    @contextmanager
    def session(self) -> Iterator[ResearchNoASession]:
        session = self._create_session()
        try:
            yield session
        finally:
            session.close()

    def run(self) -> ResearchSmokeReport | None:
        with self.session() as session:
            return session.run()

    def _create_session(self) -> ResearchNoASession:
        client = self._client
        if client is None:
            client = carla.Client(self._config.host, self._config.port)
            client.set_timeout(self._config.timeout)
        world = client.get_world()
        world_map = world.get_map()
        spawn_transform, spawn_waypoint = self._select_spawn(world_map)
        blueprint = world.get_blueprint_library().find(self._config.vehicle_blueprint)
        if blueprint.has_attribute("role_name"):
            blueprint.set_attribute("role_name", "research_noa")
        hero = world.spawn_actor(blueprint, spawn_transform)
        viewer = None
        ownership_transferred = False
        try:
            bundle = build_noa_runtime(hero, world_map, self._config.control_config)
            viewer = self._create_viewer(world, hero)
            viewer.attach()
            live_scheduler = None
            if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
                live_scheduler = ResearchAutomationScheduler(
                    bundle.automation_runtime,
                    bundle.ownership_backend,
                    hero,
                )
            session = ResearchNoASession(
                world_map=world_map,
                hero=hero,
                viewer=viewer,
                bundle=bundle,
                config=self._config,
                spawn_transform=spawn_transform,
                spawn_waypoint=spawn_waypoint,
                dry_scheduler=ResearchDryRunScheduler(bundle.automation_runtime),
                live_scheduler=live_scheduler,
            )
            ownership_transferred = True
            return session
        finally:
            if not ownership_transferred:
                try:
                    if viewer is not None:
                        viewer.close()
                finally:
                    _destroy_owned_hero(hero)

    def _select_spawn(
        self,
        world_map: ResearchNoAMap,
    ) -> tuple[carla.Transform, CarlaNoARuntimeWaypoint]:
        spawn_points = world_map.get_spawn_points()
        index = self._config.spawn_index
        if index is None:
            index = 0
        if index >= len(spawn_points):
            raise ResearchNoAPreflightError(
                f"spawn index {index} is outside 0..{len(spawn_points) - 1}"
            )
        transform = spawn_points[index]
        waypoint = world_map.get_waypoint(
            transform.location,
            project_to_road=False,
            lane_type=carla.LaneType.Driving,
        )
        if waypoint is None or waypoint.lane_type != carla.LaneType.Driving:
            raise ResearchNoAPreflightError(
                f"spawn index {index} is not on a Driving lane"
            )
        return transform, waypoint

    def _create_viewer(
        self,
        world: ResearchNoAWorld,
        hero: ResearchNoAVehicle,
    ) -> ResearchNoAViewer:
        config = DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL)
        if self._viewer_factory is not None:
            return self._viewer_factory(world, hero, config)
        if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
            return ResearchLiveDriverView(world, hero, config)
        return ResearchDriverView(world, hero, config)


def _destroy_owned_hero(hero: ResearchNoAVehicle) -> None:
    if hero.destroy() is False:
        raise ResearchNoACleanupError("owned research vehicle destroy failed")
