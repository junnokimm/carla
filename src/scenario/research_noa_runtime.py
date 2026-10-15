from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from time import monotonic, perf_counter

import carla

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction import (
    AutomationInteractionController,
    AutomationInteractionEvent,
    DriverInput,
)
from src.experiment.automation_interaction_types import AutomationInteractionEventType
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
)
from src.experiment.exit_route import ExitRoute, ExitRouteError
from src.experiment.noa_runtime import CarlaNoARuntimeWaypoint, build_noa_runtime
from src.experiment.session_clock import SessionClock
from src.logging.csv_logger import ResearchCsvLogger
from src.p6.clock import SessionOrigin
from src.p6.runtime import P6Runtime
from src.p8.native_tm_diagnostics import (
    NativeTrafficManagerDiagnosticsCollector,
    NativeTrafficManagerDiagnosticsContext,
    NativeTrafficManagerDiagnosticsStatus,
    native_tm_diagnostic_path,
)
from src.p8.tm_runtime_attestation import (
    RuntimeAttestation,
    TrafficManagerRuntimeAttestor,
    require_native_diagnostic_attestation,
)
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitObservationSource,
    ResearchExitViewBinding,
)
from src.scenario.research_exit_spawn import validate_exit_spawn
from src.scenario.research_lane_change import (
    LaneChangeCorrelation,
    ResearchLaneChangeInteractionObserver,
)
from src.scenario.research_noa_cleanup import destroy_owned_hero
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_control_delivery import (
    ControlSender,
    make_checked_control_sender,
)
from src.scenario.research_noa_diagnostics import (
    PerformanceClock,
    ResearchNoADiagnostics,
)
from src.scenario.research_noa_inventory import RehearsalInventoryPreflight
from src.scenario.research_noa_lifecycle import retain_first_error
from src.scenario.research_noa_log_setup import OpenResearchLog, open_research_log
from src.scenario.research_noa_persistence import (
    PersistedInteractionObserver,
    ResearchLogPaths,
    ResearchNoAObservationSource,
    ResearchNoAPersistence,
    build_study_run,
)
from src.scenario.research_noa_report import ResearchSmokeReport
from src.scenario.research_noa_session import (
    ResearchNoAPreflightError,
    ResearchNoASession,
)
from src.scenario.research_noa_smoke import (
    ResearchAutomationScheduler,
    ResearchDryRunScheduler,
    ResearchNoAActualSpeedSafetyError,
    ResearchNoAPiTracePrinter,
    validate_live_smoke_speed_kmh,
)
from src.scenario.research_noa_speed_policy import RESEARCH_MANUAL_MAX_THROTTLE
from src.scenario.research_noa_traffic_manager import (
    TrafficManagerScheduler,
    build_traffic_manager_noa_runtime,
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
from src.scenario.research_traffic_light import (
    ResearchTrafficLightObserver,
    TrafficLightRuntimeMetadata,
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
        borrowed_log: OpenResearchLog | None = None,
        tm_runtime_attestor: TrafficManagerRuntimeAttestor | None = None,
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
        self._borrowed_log = borrowed_log
        self._tm_runtime_attestor = (
            tm_runtime_attestor or TrafficManagerRuntimeAttestor()
        )
        self.log_paths: ResearchLogPaths | None = None
        self.native_tm_diagnostics_status: (
            NativeTrafficManagerDiagnosticsStatus | None
        ) = None

    @property
    def client_source(self) -> str:
        return "REAL_CARLA_RPC" if self._client is None else "INJECTED_TEST_CLIENT"

    @property
    def driver_input_source(self) -> str:
        if self._driver_input_source is not None:
            return "INJECTED_TEST_DRIVER_INPUT"
        if self._viewer_factory is not None:
            return "INJECTED_TEST_VIEWER"
        if self._config.tsxw_config is not None:
            return "REAL_TSXW_WITH_KEYBOARD_AXIS_OVERRIDE"
        return "REAL_KEYBOARD_PATH"

    @property
    def viewer_source(self) -> str:
        return (
            "INJECTED_TEST_VIEWER"
            if self._viewer_factory is not None
            else "REAL_DRIVER_VIEW"
        )

    @property
    def config(self) -> ResearchNoARunConfig:
        return self._config

    @property
    def borrowed_log(self) -> OpenResearchLog | None:
        return self._borrowed_log

    @contextmanager
    def session(self) -> Iterator[ResearchNoASession]:
        persistence = self._config.persistence
        opened = self._borrowed_log
        owns_logger = False
        if opened is None and persistence is not None:
            opened = open_research_log(persistence, self._monotonic_ns, self._utc_ns)
            owns_logger = True
        if opened is not None and persistence is None:
            raise ResearchNoAPreflightError(
                "borrowed research log requires persistence config"
            )
        if (
            opened is not None
            and persistence is not None
            and opened.study_run != build_study_run(persistence)
        ):
            raise ResearchNoAPreflightError(
                "borrowed research log context does not match persistence config"
            )
        logger = None if opened is None else opened.logger
        self.log_paths = None if opened is None else opened.paths
        p6 = None
        try:
            if self._config.p6 is not None:
                if opened is None or persistence is None:
                    raise ResearchNoAPreflightError("P6 requires an open research log")
                origin = opened.session_clock.origin
                p6 = P6Runtime(
                    config=self._config.p6,
                    run_id=persistence.run_id,
                    output_dir=persistence.output_dir,
                    origin=SessionOrigin(origin.monotonic_ns, origin.utc_ns),
                    monotonic_ns=self._monotonic_ns,
                    utc_ns=self._utc_ns,
                )
            with closing(
                self._create_session(
                    logger,
                    p6,
                    None if opened is None else opened.session_clock,
                )
            ) as session:
                yield session
        finally:
            inherited_error = sys.exception()
            primary_error = inherited_error
            if p6 is not None:
                primary_error = retain_first_error(p6.close, primary_error)
            if owns_logger and logger is not None:
                primary_error = retain_first_error(logger.close, primary_error)
            if inherited_error is None and primary_error is not None:
                raise primary_error

    def run(self) -> ResearchSmokeReport | None:
        with self.session() as session:
            return session.run()

    def _create_session(
        self,
        logger: ResearchCsvLogger | None = None,
        p6: P6Runtime | None = None,
        session_clock: SessionClock | None = None,
    ) -> ResearchNoASession:
        effective_speed_policy = None
        traffic_manager_port = self._config.traffic_manager_port
        prepared_attestation = (
            None
            if traffic_manager_port is None
            else self._tm_runtime_attestor.verify_before_creation(traffic_manager_port)
        )
        if (client := self._client) is None:
            client = carla.Client(self._config.host, self._config.port)
            client.set_timeout(self._config.timeout)
        world = client.get_world()
        diagnostics = ResearchNoADiagnostics(self._performance_clock)
        diagnostics.set_control_mode(DrivingControlMode.MANUAL)
        if (
            self._config.mode is ResearchNoARunMode.LIVE_SMOKE
            and (
                self._borrowed_log is not None
                or self._config.traffic_manager_port is not None
            )
            and world.get_settings().synchronous_mode
        ):
            raise ResearchNoAPreflightError(
                "operator live runtime requires synchronous_mode=false"
            )
        traffic_manager = None
        tm_attestation: RuntimeAttestation | None = None
        if traffic_manager_port is not None:
            prepared_attestation = self._tm_runtime_attestor.verify_before_creation(
                traffic_manager_port
            )
            traffic_manager = client.get_trafficmanager(traffic_manager_port)
            if prepared_attestation is None:
                raise ResearchNoAPreflightError(
                    "Traffic Manager attestation is missing"
                )
            tm_attestation = self._tm_runtime_attestor.attest_after_creation(
                prepared_attestation
            )
        applied_weather = None
        if self._config.operator_clear_noon:
            world.set_weather(carla.WeatherParameters.ClearNoon)
            applied_weather = world.get_weather()
        world_map = world.get_map()
        spawn_transform, spawn_waypoint = self._select_spawn(world_map)
        exit_config = self._config.exit_assistance
        exit_route = None
        if exit_config is not None:
            manifest = load_exit_route_manifest(exit_config.manifest_path)
            if world_map.name != manifest.map_name:
                raise ResearchNoAPreflightError(
                    f"exit manifest map {manifest.map_name} does not match {world_map.name}"
                )
            exit_route = manifest.route(exit_config.route_id)
        if (
            self._borrowed_log is not None
            and self._config.mode is ResearchNoARunMode.LIVE_SMOKE
            and exit_route is not None
        ):
            try:
                validate_exit_spawn(exit_route, spawn_transform, spawn_waypoint)
            except ExitRouteError as error:
                raise ResearchNoAPreflightError(
                    f"exit route spawn is unsuitable: {error}"
                ) from error
        blueprint = world.get_blueprint_library().find(self._config.vehicle_blueprint)
        if blueprint.has_attribute("role_name"):
            blueprint.set_attribute("role_name", "research_noa")
        if traffic_manager is not None:
            if not blueprint.has_attribute("sticky_control"):
                raise ResearchNoAPreflightError(
                    "hero blueprint does not support sticky_control"
                )
            blueprint.set_attribute("sticky_control", "true")
        hero = world.spawn_actor(blueprint, spawn_transform)
        control_sender = (
            None
            if traffic_manager is None
            else make_checked_control_sender(
                client,
                hero.id,
                timing_callback=lambda duration: diagnostics.record_operation_duration(
                    "manual_batch_rpc_wait", duration
                ),
                clock=diagnostics.timestamp,
            )
        )
        viewer = None
        native_tm_collector = None
        ownership_transferred = False
        try:
            persistence: ResearchNoAPersistence | None = None
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
                    study_run=(
                        None
                        if self._borrowed_log is None
                        else self._borrowed_log.study_run
                    ),
                    diagnostics=diagnostics,
                    exit_context=None if exit_config is None else exit_config.context,
                )
                if applied_weather is not None:
                    persistence.record_phase2_event(
                        "p8_weather_applied",
                        json.dumps(
                            {
                                "preset": "ClearNoon",
                                "scope": "P8_OPERATOR_STAGE_START",
                                "observed": {
                                    name: getattr(applied_weather, name, None)
                                    for name in (
                                        "cloudiness",
                                        "precipitation",
                                        "precipitation_deposits",
                                        "wind_intensity",
                                        "sun_azimuth_angle",
                                        "sun_altitude_angle",
                                        "fog_density",
                                        "fog_distance",
                                        "wetness",
                                    )
                                },
                            }
                        ),
                        source.get_host_timestamp(),
                    )
                    persistence.flush_events()
                if tm_attestation is not None:
                    persistence.record_phase2_event(
                        "tm_runtime_attestation",
                        json.dumps(
                            {
                                "port": tm_attestation.port,
                                "owner_pid": tm_attestation.owner_pid,
                                "libcarla_path": str(tm_attestation.libcarla_path),
                                "libcarla_sha256": tm_attestation.libcarla_sha256,
                                "status": "VERIFIED_LOCAL_PROCESS",
                                "external_client_exclusion": "OPERATING_PREREQUISITE",
                                "physical_validation": "UNVERIFIED",
                            }
                        ),
                        source.get_host_timestamp(),
                    )
                    persistence.flush_events()
            if self._config.native_tm_diagnostics is not None:
                if traffic_manager is None or tm_attestation is None:
                    raise ResearchNoAPreflightError(
                        "native TM diagnostics require an attested Traffic Manager"
                    )
                if persistence is None or session_clock is None:
                    raise ResearchNoAPreflightError(
                        "native TM diagnostics require persistence and a session clock"
                    )
                inventory_rejection = RehearsalInventoryPreflight(
                    persistence, self._config.timeout, self._monotonic_clock
                ).check(world, hero)
                if inventory_rejection is not None:
                    raise ResearchNoAPreflightError(inventory_rejection)
                require_native_diagnostic_attestation(tm_attestation)
                self._tm_runtime_attestor.verify_owned(tm_attestation)
                context = NativeTrafficManagerDiagnosticsContext(
                    participant_id=str(persistence.study_run.participant_id),
                    run_id=str(persistence.study_run.study_run_id),
                    originating_stage=persistence.config.stage_label,
                    session_clock=session_clock,
                    measurement_phase=lambda: persistence.segment.phase.value,
                    host_monotonic_ns=self._monotonic_ns,
                    host_utc_ns=self._utc_ns,
                )
                sidecar_path = native_tm_diagnostic_path(
                    persistence.config.output_dir,
                    persistence.config.stage_label,
                    hero.id,
                )
                try:
                    native_tm_collector = NativeTrafficManagerDiagnosticsCollector.open(
                        traffic_manager,
                        actor_id=hero.id,
                        output_dir=persistence.config.output_dir,
                        attestation=tm_attestation,
                        config=self._config.native_tm_diagnostics,
                        context=context,
                        monotonic_clock=self._monotonic_clock,
                    )
                except (
                    KeyboardInterrupt,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as error:
                    self.native_tm_diagnostics_status = (
                        NativeTrafficManagerDiagnosticsStatus(
                            str(sidecar_path),
                            hero.id,
                            persistence.config.stage_label,
                            persistence.segment.phase.value,
                            None,
                            "FAILED_OBSERVER",
                            False,
                            0,
                            0,
                            0,
                            0,
                            f"{type(error).__name__}:{error}",
                            tuple(getattr(error, "__notes__", ())),
                        )
                    )
                    raise
                self.native_tm_diagnostics_status = native_tm_collector.status
            route_reference = None
            if exit_config is not None:
                if exit_route is None:
                    raise ResearchNoAPreflightError(
                        "exit route preflight is incomplete"
                    )
                self._exit_route_validator(exit_route, world_map, hero)
                route_reference = SwitchableRouteLaneGeometrySource(
                    CarlaLaneGeometryAdapter(world_map)
                )
            bundle = (
                build_noa_runtime(
                    hero,
                    world_map,
                    self._config.control_config,
                    control_lane_geometry=route_reference,
                )
                if traffic_manager is None
                else build_traffic_manager_noa_runtime(
                    hero,
                    world_map,
                    traffic_manager,
                    world=world,
                    ownership_verifier=self._tm_runtime_attestor,
                    runtime_attestation=tm_attestation,
                )
            )
            viewer = self._create_viewer(
                world,
                hero,
                lambda: bundle.automation_runtime.state,
                control_sender,
                persistence,
            )
            viewer.attach()
            if self._config.camera_diagnostics:
                viewer.capture_camera_diagnostics_environment(world)
            live_scheduler = None
            interaction_events: list[AutomationInteractionEvent] = []
            interaction: (
                AutomationInteractionController
                | PersistedInteractionObserver
                | ResearchLaneChangeInteractionObserver
                | ResearchExitInteractionObserver
                | None
            ) = None
            lane_change_interaction: ResearchLaneChangeInteractionObserver | None = None
            exit_assistance = None
            exit_view_binding = None
            if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
                custom_control_backend = bundle.custom_control_backend
                if traffic_manager is None:
                    if custom_control_backend is None:
                        raise ResearchNoAPreflightError(
                            "custom control backend is missing"
                        )
                    effective_speed_policy = self._config.effective_speed_policy
                    trace_callbacks: list[Callable[[CarlaNoAControlStep], None]] = []
                    if self._control_trace is not None:
                        trace_callbacks.append(self._control_trace)
                    if self._config.pi_trace:
                        trace_callbacks.append(ResearchNoAPiTracePrinter())
                        if persistence is not None:
                            trace_callbacks.append(
                                persistence.record_control_saturation
                            )

                    def emit_control_trace(step: CarlaNoAControlStep) -> None:
                        for callback in trace_callbacks:
                            callback(step)

                    live_scheduler = ResearchAutomationScheduler(
                        bundle.automation_runtime,
                        custom_control_backend,
                        hero,
                        world,
                        effective_speed_policy,
                        emit_control_trace if trace_callbacks else None,
                        diagnostics,
                    )
                else:
                    live_scheduler = TrafficManagerScheduler(
                        bundle.automation_runtime,
                        hero,
                        diagnostics,
                        None
                        if persistence is None
                        else persistence.record_traffic_manager_speed_limit,
                    )
            if self._config.automation_interaction is not None:

                def record_event(event: AutomationInteractionEvent) -> None:
                    interaction_events.append(event)
                    if (
                        self._config.activation_speed_hold
                        and event.event_type
                        is AutomationInteractionEventType.TRANSITION
                        and event.state.control_mode is DrivingControlMode.NOA_ACTIVE
                    ):
                        if (
                            persistence is None
                            or effective_speed_policy is None
                            or custom_control_backend is None
                        ):
                            raise ResearchNoAPreflightError(
                                "activation speed hold persistence is missing"
                            )
                        if self._config.exit_assistance is None:
                            persistence.flush_automation_events()
                            observation = persistence.capture()
                        else:
                            # P5 may enqueue an earlier input-receipt event after this callback.
                            observation = persistence.source.get_observation()
                        persistence.record_automation(event, observation.timestamp)
                        captured_speed_kmh = observation.state.speed_kmh
                        try:
                            validate_live_smoke_speed_kmh(
                                captured_speed_kmh,
                                effective_speed_policy.guard_for_mode(
                                    DrivingControlMode.NOA_ACTIVE
                                ),
                                source="ACTIVATION_SPEED_HOLD",
                            )
                        except ResearchNoAActualSpeedSafetyError:
                            persistence.record_activation_speed_hold(
                                effective_speed_policy,
                                observation,
                                applied_target_speed_kmh=None,
                                status="BLOCKED",
                            )
                            raise
                        custom_control_backend.set_target_speed_kmh(captured_speed_kmh)
                        persistence.record_activation_speed_hold(
                            effective_speed_policy,
                            observation,
                            applied_target_speed_kmh=captured_speed_kmh,
                            status="APPLIED",
                        )
                    elif persistence is not None:
                        persistence.record_automation(event)
                    if self._automation_event_sink is not None:
                        self._automation_event_sink(event)

                interaction = AutomationInteractionController(
                    runtime=bundle.automation_runtime,
                    geometry=bundle.lane_geometry,
                    vehicle=hero,
                    steering_target=bundle.steering_target,
                    config=self._config.automation_interaction.policy,
                    event_sink=record_event,
                )
                if persistence is not None:
                    interaction = PersistedInteractionObserver(
                        interaction,
                        persistence,
                        None
                        if native_tm_collector is None
                        else native_tm_collector.poll,
                    )
                    if traffic_manager is not None:
                        traffic_manager_backend = bundle.traffic_manager_backend
                        if traffic_manager_backend is None:
                            raise ResearchNoAPreflightError(
                                "Traffic Manager authority backend is missing"
                            )
                        lane_change_interaction = ResearchLaneChangeInteractionObserver(
                            interaction,
                            bundle.adjacent_lanes,
                            hero,
                            persistence,
                            LaneChangeCorrelation(
                                "GENERAL",
                                None
                                if exit_config is None
                                else exit_config.lc_event_id,
                                None
                                if exit_config is None
                                else exit_config.lateral_onset_threshold_m,
                            ),
                            ResearchTrafficLightObserver(
                                hero,
                                persistence,
                                traffic_manager_backend,
                                TrafficLightRuntimeMetadata(
                                    traffic_manager_port,
                                    client.get_client_version(),
                                    client.get_server_version(),
                                    world.get_settings().synchronous_mode,
                                ),
                                diagnostics,
                            ),
                            traffic_manager_backend,
                            self._config.automation_interaction.policy,
                        )
                        interaction = lane_change_interaction
            if exit_config is not None:
                if (
                    persistence is None
                    or interaction is None
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
                        assisted_execution_enabled=(
                            exit_config.assisted_execution_enabled
                        ),
                        eligibility_open_s=exit_config.eligibility_open_s,
                        eligibility_close_s=exit_config.eligibility_close_s,
                    ),
                    route_reference,
                    persistence.record_exit,
                    session_clock=session_clock,
                    assisted_submission=(
                        None
                        if lane_change_interaction is None
                        else lane_change_interaction.submit_confirmed_p5
                    ),
                    submission_clock=persistence.source.get_host_timestamp,
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
                p6=p6,
                speed_policy=effective_speed_policy,
                control_sender=control_sender,
                native_tm_collector=native_tm_collector,
            )
            ownership_transferred = True
            return session
        finally:
            if not ownership_transferred:
                inherited_error = sys.exception()
                primary_error = inherited_error
                if native_tm_collector is not None:
                    primary_error = retain_first_error(
                        native_tm_collector.close, primary_error
                    )
                    self.native_tm_diagnostics_status = native_tm_collector.status
                if viewer is not None:
                    primary_error = retain_first_error(viewer.close, primary_error)
                primary_error = retain_first_error(
                    lambda: destroy_owned_hero(hero), primary_error
                )
                if inherited_error is None and primary_error is not None:
                    raise primary_error

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
        control_sender: ControlSender | None = None,
        persistence: ResearchNoAPersistence | None = None,
    ) -> ResearchNoAViewer:
        interaction = self._config.automation_interaction
        manual_control_limits = (
            ResearchManualControlLimits(
                RESEARCH_MANUAL_MAX_THROTTLE,
                self._config.control_config.longitudinal.max_brake,
                self._config.control_config.lateral.max_steering,
            )
            if interaction is not None
            else None
        )
        config = ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            front_camera_only=self._config.front_camera_only,
            rear_mirror_enabled=self._config.rear_mirror_enabled,
            camera_diagnostics=self._config.camera_diagnostics,
            run_id=self._config.run_id,
            driver_input_source=self._driver_input_source,
            manual_control_limits=manual_control_limits,
            automation_state_source=automation_state_source,
            training_early_finish=self._config.operator_training,
            traffic_manager_port=self._config.traffic_manager_port,
            control_sender=control_sender,
            tsxw_config=self._config.tsxw_config,
            tsxw_event_sink=(
                None
                if persistence is None
                else lambda event: persistence.record_phase2_event(
                    event.event_type,
                    event.payload_json,
                    persistence.source.get_host_timestamp(),
                )
            ),
            noa_toggle_key=(
                ord(interaction.noa_key) if interaction is not None else ord("n")
            ),
        )
        if self._viewer_factory is not None:
            return self._viewer_factory(world, hero, config)
        if self._config.mode is ResearchNoARunMode.LIVE_SMOKE:
            return ResearchLiveDriverView(world, hero, config)
        return ResearchDriverView(world, hero, config)
