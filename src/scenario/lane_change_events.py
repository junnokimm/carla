from __future__ import annotations

import json
from collections.abc import Mapping
from math import isfinite
from typing import Protocol

from src.experiment.lane_change import LaneChangeDirection, LaneChangeEligibility
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.lane_change_diagnostics import geometry_diagnostics
from src.scenario.lane_change_geometry import (
    LaneChangeObservationResult,
    LaneChangeObservationStatus,
)
from src.scenario.lane_change_lifecycle import LaneChangeTracker
from src.scenario.research_noa_persistence import ResearchSnapshotSample

JsonValue = str | int | float | bool | None | list[int | None]


class Phase2EventPersistence(Protocol):
    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None: ...


class LaneChangeEventSink:
    def __init__(self, persistence: Phase2EventPersistence) -> None:
        self._persistence = persistence
        self.logging_errors: list[str] = []

    def record(
        self,
        event_type: str,
        payload: Mapping[str, JsonValue],
        timestamp: TimestampEnvelope,
    ) -> None:
        try:
            self._persistence.record_phase2_event(
                event_type,
                json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
                timestamp,
            )
        except Exception as error:
            if payload.get("context") != "AUTO_EXIT":
                raise
            if len(self.logging_errors) < 10:
                self.logging_errors.append(f"{type(error).__name__}:{error}")


def record_geometry_observation(
    events: LaneChangeEventSink,
    tracker: LaneChangeTracker,
    result: LaneChangeObservationResult,
    sample: ResearchSnapshotSample,
) -> None:
    snapshot = sample.timestamp.carla_snapshot
    events.record(
        "lane_change_geometric_sample",
        {
            "request_id": tracker.request_id,
            "context": tracker.context,
            "direction": tracker.direction.value,
            "status": result.status.value,
            "reason": result.reason,
            "previous_frame": result.previous_frame,
            "current_frame": result.current_frame,
            "frame_gap": result.frame_gap,
            "target_entered": result.target_entered,
            "observed_road_id": (
                None if result.observed_identity is None else result.observed_identity[0]
            ),
            "observed_section_id": (
                None if result.observed_identity is None else result.observed_identity[1]
            ),
            "observed_lane_id": (
                None if result.observed_identity is None else result.observed_identity[2]
            ),
            **geometry_diagnostics(result),
            "physical_completion_claimed": (
                result.status is LaneChangeObservationStatus.COMPLETED
            ),
            "expected_source_identity": list(tracker.source_identity),
            "expected_target_identity": list(tracker.target_identity),
            "actual_current_identity": [
                sample.road_id,
                sample.section_id,
                sample.lane_id,
            ],
            "actual_left_identity": [
                sample.left_road_id,
                sample.left_section_id,
                sample.left_lane_id,
            ],
            "actual_right_identity": [
                sample.right_road_id,
                sample.right_section_id,
                sample.right_lane_id,
            ],
            "actual_vehicle_x": _finite_or_none(float(sample.transform.location.x)),
            "actual_vehicle_y": _finite_or_none(float(sample.transform.location.y)),
            "actual_vehicle_heading_degrees": _finite_or_none(
                float(sample.transform.rotation.yaw)
            ),
            "actual_frame": None if snapshot is None else snapshot.frame,
            "actual_host_capture_started_monotonic_ns": (
                None if snapshot is None else snapshot.host_capture_started_monotonic_ns
            ),
            "actual_host_capture_completed_monotonic_ns": (
                None if snapshot is None else snapshot.host_capture_completed_monotonic_ns
            ),
        },
        sample.timestamp,
    )


def record_lane_change_request(
    events: LaneChangeEventSink,
    request_id: str,
    direction: LaneChangeDirection,
    disposition: str,
    eligibility: LaneChangeEligibility | None,
    context: str,
    lc_event_id: str | None,
    timestamp: TimestampEnvelope,
    control_mode: str,
    baseline: ResearchSnapshotSample | None = None,
    active_tracker: LaneChangeTracker | None = None,
) -> None:
    recovery_required = active_tracker is not None and active_tracker.unverified
    events.record(
        "lane_change_request",
        {
            "request_id": request_id,
            "direction": direction.value,
            "host_monotonic_ns": timestamp.host.monotonic_ns,
            "control_mode": control_mode,
            "context": context,
            "lc_event_id": lc_event_id,
            "disposition": disposition,
            "static_eligibility": None if eligibility is None else eligibility.eligible,
            "static_eligibility_reason": (
                "RESOURCE_UNAVAILABLE" if eligibility is None else eligibility.reason
            ),
            "dynamic_gap_status": "UNVERIFIED",
            "execution_status": disposition,
            "current_road_id": None if eligibility is None else eligibility.current_road_id,
            "current_section_id": (
                None if eligibility is None else eligibility.current_section_id
            ),
            "current_lane_id": None if eligibility is None else eligibility.current_lane_id,
            "target_road_id": None if eligibility is None else eligibility.target_road_id,
            "target_section_id": (
                None if eligibility is None else eligibility.target_section_id
            ),
            "target_lane_id": None if eligibility is None else eligibility.target_lane_id,
            "physical_completion_claimed": False,
            "baseline_vehicle_x": (
                None
                if baseline is None
                else _finite_or_none(float(baseline.transform.location.x))
            ),
            "baseline_vehicle_y": (
                None
                if baseline is None
                else _finite_or_none(float(baseline.transform.location.y))
            ),
            "baseline_vehicle_heading_degrees": (
                None
                if baseline is None
                else _finite_or_none(float(baseline.transform.rotation.yaw))
            ),
            "active_request_id": (
                None if active_tracker is None else active_tracker.request_id
            ),
            "latched_reason": (
                None if active_tracker is None else active_tracker.unverified_reason
            ),
            "pending_physical_outcome": "UNKNOWN" if recovery_required else None,
            "recovery_required": recovery_required,
            "recovery_inputs": "N_OR_BRAKE_OR_STEER" if recovery_required else None,
        },
        timestamp,
    )


def lifecycle_payload(
    tracker: LaneChangeTracker,
    lifecycle: str,
    reason: str | None,
    timestamp: TimestampEnvelope,
    result: LaneChangeObservationResult | None = None,
) -> dict[str, JsonValue]:
    identity = None if result is None else result.observed_identity
    return {
        "request_id": tracker.request_id,
        "direction": tracker.direction.value,
        "context": tracker.context,
        "lc_event_id": tracker.lc_event_id,
        "generation": tracker.generation,
        "lifecycle": lifecycle,
        "reason": reason,
        "host_monotonic_ns": timestamp.host.monotonic_ns,
        "submission_host_monotonic_ns": tracker.submission_host_monotonic_ns,
        "submission_frame": tracker.submission_frame,
        "observed_road_id": None if identity is None else identity[0],
        "observed_section_id": None if identity is None else identity[1],
        "observed_lane_id": None if identity is None else identity[2],
        **geometry_diagnostics(result),
    }


def unavailable_lifecycle_payload(
    request_id: str,
    direction: LaneChangeDirection,
    context: str,
    lc_event_id: str | None,
    generation: int,
    reason: str | None,
    timestamp: TimestampEnvelope,
) -> dict[str, JsonValue]:
    return {
        "request_id": request_id,
        "direction": direction.value,
        "context": context,
        "lc_event_id": lc_event_id,
        "generation": generation,
        "lifecycle": "UNVERIFIED",
        "reason": reason,
        "host_monotonic_ns": timestamp.host.monotonic_ns,
    }


def _finite_or_none(value: float) -> float | None:
    return value if isfinite(value) else None
