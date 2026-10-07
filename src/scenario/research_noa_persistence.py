from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, assert_never

import carla

from src.experiment.automation import AutomationState
from src.experiment.automation_interaction import (
    AutomationInteractionEvent,
    AutomationInteractionEventType,
    DriverInput,
)
from src.experiment.automation_interaction_types import ActivationFailureReason
from src.experiment.context import (
    ExperimentCondition,
    ExperimentModule,
    ExperimentPhase,
    OutcomeFamily,
    SegmentContext,
    SegmentId,
    StudyRunContext,
)
from src.experiment.events import ResearchEventRecorder
from src.experiment.exit_assistance import ExitAssistanceEvent, ExitContext
from src.experiment.exit_route import ExitRoute
from src.experiment.session_clock import SessionClockError
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.logging.csv_logger import ResearchEvent
from src.logging.research_csv_error import ResearchCsvError
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_types import ResearchNoAVehicle, ResearchNoAWorld
from src.vehicle import VehicleObservation, VehicleState
from src.vehicle.carla_exit_route import route_data_sha256
from src.vehicle.speed import calculate_speed_kmh


@dataclass(frozen=True, slots=True)
class ResearchLogPaths:
    telemetry: Path
    events: Path


class ResearchNoAActorSnapshotError(RuntimeError):
    def __init__(self, actor_id: int) -> None:
        self.actor_id = actor_id
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"CARLA snapshot does not contain research vehicle {self.actor_id}"


class ResearchPersistenceLogger(Protocol):
    def write_telemetry(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        state: VehicleState,
    ) -> None: ...

    def write_event(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        event: ResearchEvent,
    ) -> None: ...


class ResearchObservationSource(Protocol):
    def get_host_timestamp(self) -> TimestampEnvelope: ...

    def get_observation(self) -> VehicleObservation: ...


class PersistedController(Protocol):
    @property
    def state(self) -> AutomationState: ...

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState: ...

    def update(self, driver_input: DriverInput) -> AutomationState: ...

    @property
    def initialization_failure(self) -> ActivationFailureReason | None: ...

    def after_control_applied(self) -> None: ...


class DeferredPersistence(Protocol):
    def capture(self) -> VehicleObservation: ...

    def flush_automation_events(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingResearchEvent:
    timestamp: TimestampEnvelope
    event: ResearchEvent


@dataclass(frozen=True, slots=True)
class ResearchSnapshotSample:
    timestamp: TimestampEnvelope
    transform: carla.Transform
    road_id: int | None
    section_id: int | None
    lane_id: int | None
    lane_width_m: float | None
    lane_transform: carla.Transform | None
    right_lane_id: int | None
    right_lane_width_m: float | None
    right_lane_transform: carla.Transform | None
    control: carla.VehicleControl
    indicator: str


class ResearchNoAObservationSource:
    def __init__(
        self,
        world: ResearchNoAWorld,
        hero: ResearchNoAVehicle,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_ns: Callable[[], int] = time.time_ns,
        diagnostics: ResearchNoADiagnostics | None = None,
    ) -> None:
        self._world = world
        self._hero = hero
        self._monotonic_ns = monotonic_ns
        self._utc_ns = utc_ns
        self._diagnostics = diagnostics
        self.last_snapshot_sample: ResearchSnapshotSample | None = None

    def _record_duration(self, operation: str, started_at: float) -> None:
        if self._diagnostics is not None:
            self._diagnostics.record_operation_duration(
                operation,
                self._diagnostics.timestamp() - started_at,
            )

    def _started_at(self) -> float:
        return 0.0 if self._diagnostics is None else self._diagnostics.timestamp()

    def get_host_timestamp(self) -> TimestampEnvelope:
        return TimestampEnvelope(
            host=HostClockTimestamp(self._monotonic_ns(), self._utc_ns())
        )

    def get_observation(self) -> VehicleObservation:
        started_ns = self._monotonic_ns()
        operation_started_at = self._started_at()
        snapshot = self._world.get_snapshot()
        self._record_duration("snapshot_getter", operation_started_at)
        snapshot_completed_ns = self._monotonic_ns()
        simulation_seconds = float(snapshot.timestamp.elapsed_seconds)
        actor_snapshot = snapshot.find(self._hero.id)
        if actor_snapshot is None:
            raise ResearchNoAActorSnapshotError(self._hero.id)
        velocity = actor_snapshot.get_velocity()
        transform = actor_snapshot.get_transform()
        operation_started_at = self._started_at()
        control = self._hero.get_control()
        self._record_duration("control_getter", operation_started_at)
        operation_started_at = self._started_at()
        waypoint = self._world.get_map().get_waypoint(
            transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        self._record_duration("map_getter", operation_started_at)
        right_lane = None if waypoint is None else waypoint.get_right_lane()
        operation_started_at = self._started_at()
        lights = self._hero.get_light_state()
        self._record_duration("light_getter", operation_started_at)
        left = bool(lights & carla.VehicleLightState.LeftBlinker)
        right = bool(lights & carla.VehicleLightState.RightBlinker)
        completed_ns = self._monotonic_ns()
        timestamp = TimestampEnvelope(
            host=HostClockTimestamp(completed_ns, self._utc_ns()),
            carla_snapshot=CarlaSnapshotTimestamp(
                simulation_seconds,
                int(snapshot.frame),
                started_ns,
                snapshot_completed_ns,
            ),
        )
        indicator = (
            "hazard"
            if left and right
            else "left"
            if left
            else "right"
            if right
            else "off"
        )
        self.last_snapshot_sample = ResearchSnapshotSample(
            timestamp,
            transform,
            None if waypoint is None else int(waypoint.road_id),
            None if waypoint is None else int(waypoint.section_id),
            None if waypoint is None else int(waypoint.lane_id),
            None if waypoint is None else float(waypoint.lane_width),
            None if waypoint is None else waypoint.transform,
            None if right_lane is None else int(right_lane.lane_id),
            None if right_lane is None else float(right_lane.lane_width),
            None if right_lane is None else right_lane.transform,
            control,
            indicator,
        )
        return VehicleObservation(
            timestamp=timestamp,
            state=VehicleState(
                timestamp=simulation_seconds,
                speed_kmh=calculate_speed_kmh(velocity.x, velocity.y, velocity.z),
                steering=float(control.steer),
                throttle=float(control.throttle),
                brake=float(control.brake),
                lane_id=None if waypoint is None else int(waypoint.lane_id),
                indicator=indicator,
            ),
        )


class ResearchNoAPersistence:
    def __init__(
        self,
        config: ResearchPersistenceConfig,
        logger: ResearchPersistenceLogger,
        source: ResearchObservationSource,
        *,
        diagnostics: ResearchNoADiagnostics | None = None,
        exit_context: ExitContext | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.source = source
        self.study_run = build_study_run(config)
        self.segment = build_segment(config, exit_context)
        self.recorder = ResearchEventRecorder(source, logger, self.segment)
        self._pending_events: list[PendingResearchEvent] = []
        self._stage_started = False
        self._diagnostics = diagnostics

    def capture(self) -> VehicleObservation:
        observation = self.source.get_observation()
        started_at = (
            None if self._diagnostics is None else self._diagnostics.timestamp()
        )
        try:
            self.logger.write_telemetry(
                self.segment, observation.timestamp, observation.state
            )
        finally:
            if started_at is not None:
                self._diagnostics.record_operation_duration(
                    "telemetry_write", self._diagnostics.timestamp() - started_at
                )
        return observation

    def record_automation(self, event: AutomationInteractionEvent) -> None:
        event_type, event_value = map_automation_event(event)
        self.enqueue_event(
            PendingResearchEvent(
                self.source.get_host_timestamp(),
                ResearchEvent(event_type=event_type, event_value=event_value),
            )
        )

    def record_exit(self, event: ExitAssistanceEvent) -> None:
        self.enqueue_event(
            PendingResearchEvent(
                event.timestamp,
                ResearchEvent(event.event_type, event.payload_json),
            )
        )

    def record_exit_config(
        self,
        config: ResearchExitAssistanceConfig,
        route: ExitRoute,
    ) -> None:
        payload = json.dumps(
            {
                "approval_claimed": False,
                "confirm_key": config.confirm_key,
                "context": config.context.value,
                "development_values_not_policy": True,
                "development_route_validation": {
                    "maximum_sample_spacing_m": 6.25,
                    "maximum_tangent_delta_rad": 0.2,
                    "requires_corridor_match": True,
                    "requires_immediate_right_adjacency": True,
                    "requires_lane_change_permission": True,
                    "requires_same_direction": True,
                    "requires_vehicle_fit": True,
                },
                "lateral_onset_threshold_m": config.lateral_onset_threshold_m,
                "lc_event_id": config.lc_event_id,
                "navigation_trigger_distance_m": config.navigation_trigger_distance_m,
                "navigation_start_m": route.navigation_start,
                "navigation_end_m": route.navigation_end,
                "confirmation_start_m": route.confirmation_start,
                "confirmation_end_m": route.confirmation_end,
                "maneuver_start_m": route.initiation_start_m,
                "maneuver_end_m": route.initiation_end_m,
                "reject_key": config.reject_key,
                "response_timeout_s": config.response_timeout_s,
                "route_id": route.route_id,
                "route_version": route.route_version,
                "route_semantic_sha256": route_data_sha256((route,)),
                "source_lane_id": route.source_lane_id,
                "target_lane_id": route.target_lane_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        self._record_event(
            self.source.get_host_timestamp(),
            ResearchEvent("p5_exit_manifest", payload),
        )

    def enqueue_event(self, event: PendingResearchEvent) -> None:
        self._pending_events.append(event)

    def flush_events(self) -> None:
        self._pending_events.sort(key=lambda item: item.timestamp.host.monotonic_ns)
        while self._pending_events:
            pending = self._pending_events[0]
            self._record_event(pending.timestamp, pending.event)
            del self._pending_events[0]

    def flush_automation_events(self) -> None:
        self.flush_events()

    def _record_event(self, timestamp: TimestampEnvelope, event: ResearchEvent) -> None:
        started_at = (
            None if self._diagnostics is None else self._diagnostics.timestamp()
        )
        try:
            self.recorder.record(timestamp, event)
        finally:
            if started_at is not None:
                self._diagnostics.record_operation_duration(
                    "event_write", self._diagnostics.timestamp() - started_at
                )

    @property
    def stage_started(self) -> bool:
        return self._stage_started

    def start_stage_attempt(self) -> None:
        timestamp = self.source.get_host_timestamp()
        self._record_event(
            timestamp,
            ResearchEvent("stage_start_attempt", self.config.stage_label),
        )
        self._record_event(
            timestamp,
            ResearchEvent(
                "telemetry_schema",
                "speed=snapshot_actor;control=host_rpc_after_snapshot;"
                "transform=host_rpc_after_snapshot;lane=host_rpc_after_snapshot;"
                "lights=host_rpc_after_snapshot;"
                "auxiliary_host_interval=[carla_capture_completed_host_monotonic_ns,"
                "host_monotonic_ns];carla_frame_applies_to_speed_only",
            ),
        )

    def start_stage(self) -> None:
        self._record_event(
            self.source.get_host_timestamp(),
            ResearchEvent("stage_start", self.config.stage_label),
        )
        self._stage_started = True

    def fail_stage_start(self, reason: str) -> None:
        self._record_event(
            self.source.get_host_timestamp(),
            ResearchEvent("stage_start_failure", f"{self.config.stage_label}:{reason}"),
        )

    def end_stage(self, interruption_reason: str) -> None:
        self._record_event(
            self.source.get_host_timestamp(),
            ResearchEvent(
                "stage_end", f"{self.config.stage_label}:{interruption_reason}"
            ),
        )

    def record_shutdown(
        self,
        reason: str,
        state: AutomationState,
        *,
        timestamp: TimestampEnvelope | None = None,
    ) -> None:
        if timestamp is None:
            timestamp = self.source.get_host_timestamp()
        self._record_event(
            timestamp,
            ResearchEvent(
                "auto_state",
                f"{state.availability.value}|{state.control_mode.value}",
            ),
        )
        self._record_event(
            timestamp,
            ResearchEvent("disengagement", reason),
        )


class PersistedInteractionObserver:
    def __init__(
        self,
        controller: PersistedController,
        persistence: DeferredPersistence,
    ) -> None:
        self._controller = controller
        self._persistence = persistence
        self._pending_driver_input: DriverInput | None = None
        self.last_observation: VehicleObservation | None = None

    @property
    def state(self) -> AutomationState:
        return self._controller.state

    @property
    def initialization_failure(self) -> ActivationFailureReason | None:
        return self._controller.initialization_failure

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        try:
            state = self._controller.initialize(
                module,
                condition,
                lane_change_in_progress=lane_change_in_progress,
                stage=stage,
            )
        finally:
            primary_error = sys.exception()
            try:
                self._persistence.flush_automation_events()
            except (OSError, RuntimeError, ResearchCsvError, SessionClockError):
                if primary_error is None:
                    raise
        self.last_observation = self._persistence.capture()
        return state

    def update(self, driver_input: DriverInput) -> AutomationState:
        self._pending_driver_input = driver_input
        return self._controller.update(driver_input)

    def after_control_applied(self) -> None:
        driver_input = self._pending_driver_input
        if driver_input is None:
            return
        try:
            self._controller.after_control_applied()
        finally:
            primary_error = sys.exception()
            self._pending_driver_input = None
            try:
                self._persistence.flush_automation_events()
            except (OSError, RuntimeError, ResearchCsvError, SessionClockError):
                if primary_error is None:
                    raise
        self.last_observation = self._persistence.capture()


def build_study_run(config: ResearchPersistenceConfig) -> StudyRunContext:
    assignment = config.assignment
    return StudyRunContext(
        study_run_id=config.run_id,
        participant_id=assignment.participant_id,
        module_1_condition=assignment.m1_condition,
        module_2_condition=assignment.m2_condition,
        route_id=assignment.route_id,
        scenario_version=assignment.scenario_version,
        aoi_file_version=assignment.aoi_file_version,
        program_version=assignment.program_version,
    )


def build_segment(
    config: ResearchPersistenceConfig,
    exit_context: ExitContext | None = None,
) -> SegmentContext:
    if exit_context is ExitContext.TRAINING:
        return SegmentContext(
            segment_id=SegmentId(f"{config.run_id}-training"),
            study_run_id=config.run_id,
            phase=ExperimentPhase.TRAINING,
            module=None,
            condition=None,
            outcome_family=None,
            route=config.assignment.route_id,
        )
    match config.module:
        case ExperimentModule.MODULE_1:
            phase = ExperimentPhase.MODULE_1
            outcome = OutcomeFamily.AUTOMATION_CHOICE
        case ExperimentModule.MODULE_2:
            phase = ExperimentPhase.MODULE_2
            outcome = OutcomeFamily.ATTENTION_ALLOCATION
        case unreachable:
            assert_never(unreachable)
    return SegmentContext(
        segment_id=SegmentId(f"{config.run_id}-{config.module.value.lower()}"),
        study_run_id=config.run_id,
        phase=phase,
        module=config.module,
        condition=config.condition,
        outcome_family=outcome,
        route=config.assignment.route_id,
    )


def assignment_event_value(config: ResearchPersistenceConfig) -> str:
    assignment = config.assignment
    return json.dumps(
        {
            "participant_id": assignment.participant_id,
            "m1_condition": assignment.m1_condition.value,
            "m2_condition": assignment.m2_condition.value,
            "route_id": assignment.route_id,
            "scenario_version": assignment.scenario_version,
            "aoi_file_version": assignment.aoi_file_version,
            "program_version": assignment.program_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def map_automation_event(event: AutomationInteractionEvent) -> tuple[str, str]:
    state = f"{event.state.availability.value}|{event.state.control_mode.value}"
    match event.event_type:
        case AutomationInteractionEventType.INITIAL_STATE:
            return "initial_state", state
        case AutomationInteractionEventType.REQUEST:
            requested = event.requested_mode
            return "auto_request", "" if requested is None else requested.value
        case AutomationInteractionEventType.TRANSITION:
            return "auto_state", state
        case AutomationInteractionEventType.FAILURE:
            reason = event.failure_reason
            return "activation_failure", "" if reason is None else reason.value
        case AutomationInteractionEventType.DISENGAGEMENT:
            reason = event.deactivation_reason
            return "disengagement", "" if reason is None else reason.value
        case AutomationInteractionEventType.AVAILABILITY:
            reason = event.failure_reason
            suffix = "" if reason is None else f":{reason.value}"
            return "availability", f"{event.state.availability.value}{suffix}"
        case unreachable:
            assert_never(unreachable)
