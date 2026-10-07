from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from time import monotonic, perf_counter

import carla

from src.experiment.automation import AutomationState
from src.experiment.automation_interaction import (
    AutomationInteractionController,
    AutomationInteractionEvent,
    DriverInput,
)
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
)
from src.experiment.exit_route import ExitRoute
from src.experiment.noa_runtime import CarlaNoARuntimeWaypoint, build_noa_runtime
from src.logging.csv_logger import ResearchCsvLogger
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitObservationSource,
    ResearchExitViewBinding,
)
from src.scenario.research_noa_cleanup import destroy_owned_hero
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_diagnostics import (
    PerformanceClock,
    ResearchNoADiagnostics,
)
from src.scenario.research_noa_log_setup import open_research_log
from src.scenario.research_noa_persistence import (
    PersistedInteractionObserver,
    ResearchLogPaths,
    ResearchNoAObservationSource,
    ResearchNoAPersistence,
)
from src.scenario.research_noa_report import ResearchSmokeReport
from src.scenario.research_noa_session import (
    ResearchNoAPreflightError,
    ResearchNoASession,
)
from src.scenario.research_noa_smoke import (
    ResearchAutomationScheduler,
    ResearchDryRunScheduler,
    ResearchNoAPiTracePrinter,
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
    ResearchManualControlLimits,
)
from src.vehicle.carla_exit_route import (
    SwitchableRouteLaneGeometrySource,
    load_exit_route_manifest,
    validate_exit_route_map,
)
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryAdapter
from src.vehicle.carla_noa_simulation_control import CarlaNoAControlStep
from src.vehicle.driving_mode import DrivingMode


class ResearchNoARunner:
    def __init__(
        self,
        config: ResearchNoARunConfig,
        *,
        client: ResearchNoAClient | None = None,
        viewer_factory: ResearchNoAViewerFactory | None = None,
        monotonic_clock: MonotonicClock = monotonic,
        control_trace: Callable[[CarlaNoAControlStep], None] | None = None,
        driver_input_source: Callable[[], DriverInput] | None = None,
        automation_event_sink: Callable[[AutomationInteractionEvent], None]
        | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_ns: Callable[[], int] = time.time_ns,
        performance_clock: PerformanceClock = perf_counter,
        exit_route_validator: Callable[
            [ExitRoute, ResearchNoAMap, ResearchNoAVehicle], None
        ] = validate_exit_route_map,
    ) -> None:
        self._config = config
        self._client = client
        self._viewer_factory = viewer_factory
        self._monotonic_clock = monotonic_clock
        self._control_trace = control_trace
        self._driver_input_source = driver_input_source
        self._automation_event_sink = automation_event_sink
        self._monotonic_ns = monotonic_ns
        self._utc_ns = utc_ns
        self._performance_clock = performance_clock
        self._exit_route_validator = exit_route_validator
        self.log_paths: ResearchLogPaths | None = None

    @contextmanager
    def session(self) -> Iterator[ResearchNoASession]:
        persistence = self._config.persistence
        opened = (
            None
            if persistence is None
            else open_research_log(persistence, self._monotonic_ns, self._utc_ns)
        )
        logger = None if opened is None else opened.logger
        self.log_paths = None if opened is None else opened.paths
        try:
            with closing(self._create_session(logger)) as session:
                yield session
        finally:
            if logger is not None:
                logger.close()

    def run(self) -> ResearchSmokeReport | None:
        with self.session() as session:
            return session.run()

    def _create_session(
        self, logger: ResearchCsvLogger | None = None
    ) -> ResearchNoASession:
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
            exit_config = self._config.exit_assistance
            exit_route = None
            route_reference = None
            if exit_config is not None:
                manifest = load_exit_route_manifest(exit_config.manifest_path)
                if world_map.name != manifest.map_name:
                    raise ResearchNoAPreflightError(
                        f"exit manifest map {manifest.map_name} does not match {world_map.name}"
                    )
                exit_route = manifest.route(exit_config.route_id)
                self._exit_route_validator(exit_route, world_map, hero)
                route_reference = SwitchableRouteLaneGeometrySource(
                    CarlaLaneGeometryAdapter(world_map)
                )
            bundle = build_noa_runtime(
                hero,
                world_map,
                self._config.control_config,
                control_lane_geometry=route_reference,
            )
            viewer = self._create_viewer(
                world, hero, lambda: bundle.automation_runtime.state
            )
            viewer.attach()
            if self._config.camera_diagnostics:
                viewer.capture_camera_diagnostics_environment(world)
            live_scheduler = None
            diagnostics = ResearchNoADiagnostics(self._performance_clock)
            if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
                live_scheduler = ResearchAutomationScheduler(
                    bundle.automation_runtime,
                    bundle.control_backend,
                    hero,
                    world,
                    self._control_trace
                    or (ResearchNoAPiTracePrinter() if self._config.pi_trace else None),
                    diagnostics,
                )
            interaction_events: list[AutomationInteractionEvent] = []
            interaction: (
                AutomationInteractionController
                | PersistedInteractionObserver
                | ResearchExitInteractionObserver
                | None
            ) = None
            exit_assistance = None
            exit_view_binding = None
            persistence = None
            persistence_config = self._config.persistence
            if persistence_config is not None:
                if logger is None:
                    raise ResearchNoAPreflightError("research logger is missing")
                source = ResearchNoAObservationSource(
                    world,
                    hero,
                    monotonic_ns=self._monotonic_ns,
                    utc_ns=self._utc_ns,
                    diagnostics=diagnostics,
                )
                persistence = ResearchNoAPersistence(
                    persistence_config,
                    logger,
                    source,
                    diagnostics=diagnostics,
                    exit_context=None if exit_config is None else exit_config.context,
                )
            if self._config.automation_interaction is not None:

                def record_event(event: AutomationInteractionEvent) -> None:
                    interaction_events.append(event)
                    if persistence is not None:
                        persistence.record_automation(event)
                    if self._automation_event_sink is not None:
                        self._automation_event_sink(event)

                interaction = AutomationInteractionController(
                    runtime=bundle.automation_runtime,
                    geometry=bundle.lane_geometry,
                    vehicle=hero,
                    steering_target=bundle.control_backend,
                    config=self._config.automation_interaction.policy,
                    event_sink=record_event,
                )
                if persistence is not None:
                    interaction = PersistedInteractionObserver(interaction, persistence)
            if exit_config is not None:
                if (
                    persistence is None
                    or not isinstance(interaction, PersistedInteractionObserver)
                    or exit_route is None
                    or route_reference is None
                ):
                    raise ResearchNoAPreflightError(
                        "exit assistance requires persisted interaction runtime"
                    )
                interaction_config = self._config.automation_interaction
                if interaction_config is None:
                    raise ResearchNoAPreflightError(
                        "exit assistance interaction config is missing"
                    )
                exit_assistance = ExitAssistanceCoordinator(
                    ExitAssistanceConfig(
                        module=interaction_config.module,
                        condition=interaction_config.condition,
                        context=exit_config.context,
                        lc_event_id=exit_config.lc_event_id,
                        route=exit_route,
                        navigation_trigger_distance_m=exit_config.navigation_trigger_distance_m,
                        response_timeout_s=exit_config.response_timeout_s,
                        lateral_onset_threshold_m=exit_config.lateral_onset_threshold_m,
                    ),
                    route_reference,
                    persistence.record_exit,
                )
                persistence.record_exit_config(exit_config, exit_route)
                interaction = ResearchExitInteractionObserver(
                    interaction,
                    exit_assistance,
                    ResearchExitObservationSource(exit_route, source),
                    persistence,
                )
                exit_view_binding = ResearchExitViewBinding(
                    interaction, persistence, exit_config
                )
            viewer.set_exit_view_binding(exit_view_binding)
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
                interaction=interaction,
                interaction_events=interaction_events,
                persistence=persistence,
                exit_assistance=exit_assistance,
                exit_view_binding=exit_view_binding,
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
        automation_state_source: Callable[[], AutomationState],
    ) -> ResearchNoAViewer:
        interaction = self._config.automation_interaction
        manual_control_limits = (
            ResearchManualControlLimits(
                self._config.control_config.longitudinal.max_throttle,
                self._config.control_config.longitudinal.max_brake,
                self._config.control_config.lateral.max_steering,
            )
            if interaction is not None
            else None
        )
        config = ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            front_camera_only=self._config.front_camera_only,
            camera_diagnostics=self._config.camera_diagnostics,
            run_id=self._config.run_id,
            driver_input_source=self._driver_input_source,
            manual_control_limits=manual_control_limits,
            automation_state_source=automation_state_source,
            noa_toggle_key=(
                ord(interaction.noa_key) if interaction is not None else ord("n")
            ),
        )
        if self._viewer_factory is not None:
            return self._viewer_factory(world, hero, config)
        if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
            return ResearchLiveDriverView(world, hero, config)
        return ResearchDriverView(world, hero, config)
