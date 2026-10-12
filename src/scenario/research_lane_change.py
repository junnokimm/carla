from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    DriverInput,
)
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.lane_change import LaneChangeDirection, LaneChangeEligibility
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_adjacent_lanes import (
    CarlaAdjacentLaneAdapter,
    CarlaAdjacentLaneTransform,
)


class LaneChangeSnapshotInvariantError(RuntimeError):
    pass


class LaneChangeInteraction(Protocol):
    @property
    def state(self) -> AutomationState: ...

    @property
    def initialization_failure(self) -> ActivationFailureReason | None: ...

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState: ...

    def update(self, driver_input: DriverInput) -> AutomationState: ...

    def after_control_applied(self) -> None: ...


class LaneChangeVehicle(Protocol):
    def get_transform(self) -> CarlaAdjacentLaneTransform: ...


class LaneChangeEventSource(Protocol):
    last_snapshot_sample: ResearchSnapshotSample | None

    def get_host_timestamp(self) -> TimestampEnvelope: ...


class LaneChangeEventPersistence(Protocol):
    @property
    def source(self) -> LaneChangeEventSource: ...

    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None: ...


class TrafficLightRecorder(Protocol):
    def record(
        self,
        sample: ResearchSnapshotSample,
        state: AutomationState,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LaneChangeCorrelation:
    context: str
    lc_event_id: str | None
    lateral_onset_threshold_m: float | None


@dataclass(frozen=True, slots=True)
class TrackedLaneChangeRequest:
    request_id: str
    direction: LaneChangeDirection
    target_identity: tuple[int, int, int]
    source_x: float
    source_y: float


class ResearchLaneChangeInteractionObserver:
    def __init__(
        self,
        interaction: LaneChangeInteraction,
        adjacent_lanes: CarlaAdjacentLaneAdapter,
        vehicle: LaneChangeVehicle,
        persistence: LaneChangeEventPersistence,
        correlation: LaneChangeCorrelation,
        traffic_light: TrafficLightRecorder | None = None,
    ) -> None:
        self._interaction = interaction
        self._adjacent_lanes = adjacent_lanes
        self._vehicle = vehicle
        self._persistence = persistence
        self._correlation = correlation
        self._traffic_light = traffic_light
        self._request_sequence = 0
        self._tracked_request: TrackedLaneChangeRequest | None = None
        self._last_observed_identity: (
            tuple[int | None, int | None, int | None] | None
        ) = None

    @property
    def state(self) -> AutomationState:
        return self._interaction.state

    @property
    def initialization_failure(self) -> ActivationFailureReason | None:
        return self._interaction.initialization_failure

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        return self._interaction.initialize(
            module,
            condition,
            lane_change_in_progress=lane_change_in_progress,
            stage=stage,
        )

    def update(self, driver_input: DriverInput) -> AutomationState:
        state = self._interaction.update(driver_input)
        direction = driver_input.lane_change_direction
        if self._tracked_request is not None and (
            state.control_mode is DrivingControlMode.MANUAL or direction is not None
        ):
            timestamp = self._persistence.source.get_host_timestamp()
            self._end_tracking(
                "MANUAL_PRIORITY"
                if state.control_mode is DrivingControlMode.MANUAL
                else "SUPERSEDED_BY_NEW_REQUEST",
                timestamp,
            )
        if direction is None:
            return state
        timestamp = self._persistence.source.get_host_timestamp()
        self._request_sequence += 1
        request_id = (
            f"lane-request:{timestamp.host.monotonic_ns}:{self._request_sequence}"
        )
        eligibility = self._adjacent_lanes.evaluate(self._vehicle, direction)
        disposition = self._disposition(driver_input, state, eligibility)
        p5_request = (
            self._correlation.lc_event_id is not None
            and direction is LaneChangeDirection.RIGHT
            and driver_input.exit_confirm_requested
        )
        payload = {
            "request_id": request_id,
            "direction": direction.value,
            "host_monotonic_ns": timestamp.host.monotonic_ns,
            "control_mode": state.control_mode.value,
            "context": "P5" if p5_request else "GENERAL",
            "lc_event_id": (self._correlation.lc_event_id if p5_request else None),
            "disposition": disposition,
            "static_eligibility": eligibility.eligible,
            "static_eligibility_reason": eligibility.reason,
            "dynamic_gap_status": "UNVERIFIED",
            "execution_status": "BLOCKED_SAFE_TM_EXECUTION_UNAVAILABLE",
            "current_road_id": eligibility.current_road_id,
            "current_section_id": eligibility.current_section_id,
            "current_lane_id": eligibility.current_lane_id,
            "target_road_id": eligibility.target_road_id,
            "target_section_id": eligibility.target_section_id,
            "target_lane_id": eligibility.target_lane_id,
            "lateral_onset_status": (
                "UNVERIFIED_NO_GENERAL_THRESHOLD"
                if self._correlation.lateral_onset_threshold_m is None
                else "UNVERIFIED_NOT_OBSERVED"
            ),
            "physical_completion_claimed": False,
        }
        self._persistence.record_phase2_event(
            "lane_change_request",
            json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
            timestamp,
        )
        if (
            disposition == "BLOCKED_SAFE_TM_EXECUTION_UNAVAILABLE"
            and eligibility.target_road_id is not None
            and eligibility.target_section_id is not None
            and eligibility.target_lane_id is not None
        ):
            source = self._vehicle.get_transform().location
            self._tracked_request = TrackedLaneChangeRequest(
                request_id,
                direction,
                (
                    eligibility.target_road_id,
                    eligibility.target_section_id,
                    eligibility.target_lane_id,
                ),
                float(source.x),
                float(source.y),
            )
            self._last_observed_identity = None
        return state

    def after_control_applied(self) -> None:
        self._interaction.after_control_applied()
        if self._traffic_light is not None:
            sample = self._persistence.source.last_snapshot_sample
            if sample is None:
                raise LaneChangeSnapshotInvariantError(
                    "post-control traffic-light snapshot is missing"
                )
            self._traffic_light.record(sample, self.state)
        tracked = self._tracked_request
        if tracked is None:
            return
        sample = self._persistence.source.last_snapshot_sample
        if sample is None:
            raise LaneChangeSnapshotInvariantError(
                "post-control lane-change snapshot is missing"
            )
        identity = (sample.road_id, sample.section_id, sample.lane_id)
        identity_changed = identity != self._last_observed_identity
        self._last_observed_identity = identity
        target_transition = identity_changed and identity == tracked.target_identity
        observed_x = float(sample.transform.location.x)
        observed_y = float(sample.transform.location.y)
        offset_x = observed_x - tracked.source_x
        offset_y = observed_y - tracked.source_y
        payload = {
            "request_id": tracked.request_id,
            "direction": tracked.direction.value,
            "host_monotonic_ns": sample.timestamp.host.monotonic_ns,
            "observed_road_id": sample.road_id,
            "observed_section_id": sample.section_id,
            "observed_lane_id": sample.lane_id,
            "observed_x": observed_x,
            "observed_y": observed_y,
            "source_relative_x_m": offset_x,
            "source_relative_y_m": offset_y,
            "source_relative_distance_m": (offset_x**2 + offset_y**2) ** 0.5,
            "observed_lane_identity_changed": identity_changed,
            "target_identity_transition": target_transition,
            "physical_boundary_crossing_verified": False,
            "lateral_onset_status": "UNVERIFIED_NO_THRESHOLD_MEASUREMENT",
            "physical_completion_claimed": False,
        }
        self._persistence.record_phase2_event(
            "lane_change_geometric_sample",
            json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
            sample.timestamp,
        )

    def finish_lane_change_observation(
        self,
        reason: str,
        timestamp: TimestampEnvelope,
    ) -> None:
        self._end_tracking(reason, timestamp)

    def _end_tracking(self, reason: str, timestamp: TimestampEnvelope) -> None:
        tracked = self._tracked_request
        if tracked is None:
            return
        self._persistence.record_phase2_event(
            "lane_change_observation_end",
            json.dumps(
                {
                    "request_id": tracked.request_id,
                    "direction": tracked.direction.value,
                    "reason": reason,
                    "host_monotonic_ns": timestamp.host.monotonic_ns,
                    "physical_completion_claimed": False,
                },
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            timestamp,
        )
        self._tracked_request = None
        self._last_observed_identity = None

    @staticmethod
    def _disposition(
        driver_input: DriverInput,
        state: AutomationState,
        eligibility: LaneChangeEligibility,
    ) -> str:
        if state.control_mode is DrivingControlMode.MANUAL:
            return "REJECTED_MANUAL_PRIORITY"
        if driver_input.lane_change_in_progress:
            return "REJECTED_ONGOING"
        if not eligibility.eligible:
            return "REJECTED_STATIC_INELIGIBLE"
        return "BLOCKED_SAFE_TM_EXECUTION_UNAVAILABLE"
