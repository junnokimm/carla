from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from time import monotonic

import carla

from src.experiment.noa_runtime import (
    CarlaNoARuntimeWaypoint,
    build_noa_runtime,
)
from src.scenario.research_noa_cleanup import destroy_owned_hero
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_report import ResearchSmokeReport
from src.scenario.research_noa_session import (
    ResearchNoAPreflightError,
    ResearchNoASession,
)
from src.scenario.research_noa_smoke import (
    ResearchAutomationScheduler,
    ResearchDryRunScheduler,
)
from src.scenario.research_noa_transmission import MonotonicClock
from src.scenario.research_noa_types import (
    ResearchNoAClient,
    ResearchNoAMap,
    ResearchNoAVehicle,
    ResearchNoAViewer,
    ResearchNoAViewerFactory,
    ResearchNoAWorld,
)
from src.scenario.research_noa_view import (
    ResearchDriverView,
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
)
from src.vehicle.driving_mode import DrivingMode


class ResearchNoARunner:
    def __init__(
        self,
        config: ResearchNoARunConfig,
        *,
        client: ResearchNoAClient | None = None,
        viewer_factory: ResearchNoAViewerFactory | None = None,
        monotonic_clock: MonotonicClock = monotonic,
    ) -> None:
        self._config = config
        self._client = client
        self._viewer_factory = viewer_factory
        self._monotonic_clock = monotonic_clock

    @contextmanager
    def session(self) -> Iterator[ResearchNoASession]:
        with closing(self._create_session()) as session:
            yield session

    def run(self) -> ResearchSmokeReport | None:
        with self.session() as session:
            return session.run()

    def _create_session(self) -> ResearchNoASession:
        if (client := self._client) is None:
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
                world=world,
                hero=hero,
                viewer=viewer,
                bundle=bundle,
                config=self._config,
                spawn_transform=spawn_transform,
                spawn_waypoint=spawn_waypoint,
                dry_scheduler=ResearchDryRunScheduler(bundle.automation_runtime),
                live_scheduler=live_scheduler,
                monotonic_clock=self._monotonic_clock,
            )
            ownership_transferred = True
            return session
        finally:
            if not ownership_transferred:
                try:
                    if viewer is not None:
                        viewer.close()
                finally:
                    destroy_owned_hero(hero)

    def _select_spawn(
        self,
        world_map: ResearchNoAMap,
    ) -> tuple[carla.Transform, CarlaNoARuntimeWaypoint]:
        spawn_points = world_map.get_spawn_points()
        index = self._config.spawn_index or 0
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
        config = ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            front_camera_only=self._config.front_camera_only,
        )
        if self._viewer_factory is not None:
            return self._viewer_factory(world, hero, config)
        if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
            return ResearchLiveDriverView(world, hero, config)
        return ResearchDriverView(world, hero, config)
