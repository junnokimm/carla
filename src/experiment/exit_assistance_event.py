from __future__ import annotations

import json
from dataclasses import dataclass

from src.experiment.automation import DrivingControlMode
from src.experiment.exit_assistance_types import (
    ExitAssistanceConfig,
    ExitAssistanceEvent,
    ExitContext,
    ExitObservation,
)
from src.experiment.timestamp import TimestampEnvelope


@dataclass(frozen=True, slots=True)
class ExitEventData:
    config: ExitAssistanceConfig
    event_type: str
    timestamp: TimestampEnvelope
    t0: TimestampEnvelope | None
    frozen_mode: DrivingControlMode | None
    actual_mode: DrivingControlMode | None
    indicator_timestamp: TimestampEnvelope | None
    steering_timestamp: TimestampEnvelope | None
    lateral_timestamp: TimestampEnvelope | None
    crossing_timestamp: TimestampEnvelope | None
    decision: str | None
    outcome: str | None
    termination_reason: str | None
    observed_proxy: bool
    input_received: bool
    observation: ExitObservation | None
    input_timestamp: TimestampEnvelope | None
    decision_commit_timestamp: TimestampEnvelope | None
    persisted_input_timestamp: TimestampEnvelope | None
    observed_proxy_timestamp: TimestampEnvelope | None
    observation_gap_previous_frame: int | None


def build_exit_event(data: ExitEventData) -> ExitAssistanceEvent:
    payload = {
        "lc_event_id": data.config.lc_event_id,
        "module": None
        if data.config.context is ExitContext.TRAINING
        else data.config.module.value,
        "context": data.config.context.value,
        "route_version": data.config.route.route_version,
        "route_id": data.config.route.route_id,
        "t0_host_monotonic_ns": None if data.t0 is None else data.t0.host.monotonic_ns,
        "noa_state_t0": None if data.frozen_mode is None else data.frozen_mode.value,
        "actual_noa_state": None
        if data.actual_mode is None
        else data.actual_mode.value,
        "input_receipt_host_monotonic_ns": data.persisted_input_timestamp.host.monotonic_ns
        if data.persisted_input_timestamp is not None
        else None,
        "decision_commit_host_monotonic_ns": data.decision_commit_timestamp.host.monotonic_ns
        if data.decision_commit_timestamp is not None
        else None,
        "observed_change_proxy_host_monotonic_ns": data.observed_proxy_timestamp.host.monotonic_ns
        if data.observed_proxy_timestamp is not None
        else None,
        "decision": data.decision,
        "indicator_onset_host_monotonic_ns": None
        if data.indicator_timestamp is None
        else data.indicator_timestamp.host.monotonic_ns,
        "steering_onset_host_monotonic_ns": None
        if data.steering_timestamp is None
        else data.steering_timestamp.host.monotonic_ns,
        "lateral_onset_host_monotonic_ns": None
        if data.lateral_timestamp is None
        else data.lateral_timestamp.host.monotonic_ns,
        "tL_host_monotonic_ns": None
        if data.crossing_timestamp is None
        else data.crossing_timestamp.host.monotonic_ns,
        "crossing_criterion": "vehicle_center_target_boundary"
        if data.crossing_timestamp is not None
        else None,
        "outcome": data.outcome,
        "termination_reason": data.termination_reason,
        "observation_frame": None
        if data.observation is None
        else data.observation.carla_frame,
        "observation_simulation_seconds": None
        if data.observation is None
        else data.observation.carla_simulation_seconds,
        "observation_gap_previous_frame": data.observation_gap_previous_frame,
        "observation_gap_current_frame": None
        if data.observation is None
        else data.observation.carla_frame,
        "observed_road_id": None if data.observation is None else data.observation.road_id,
        "observed_section_id": None if data.observation is None else data.observation.section_id,
        "observed_lane_id": None if data.observation is None else data.observation.lane_id,
        "route_progress_m": None if data.observation is None else data.observation.route_distance_m,
        "route_reference_distance_m": None if data.observation is None else data.observation.route_reference_distance_m,
        "route_reference_half_width_m": None if data.observation is None else data.observation.route_reference_half_width_m,
    }
    return ExitAssistanceEvent(
        data.event_type,
        data.timestamp,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )
