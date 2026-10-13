from __future__ import annotations

from dataclasses import dataclass

from src.experiment.automation_activation_gate import lane_alignment_failure
from src.experiment.automation_interaction_types import (
    ActivationVehicleBoundingBox,
    AutomationInteractionConfig,
)
from src.experiment.lane_change import LaneChangeDirection
from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.lane_change_geometry import (
    LaneChangeObservationResult,
    LaneChangeObservationStatus,
    LaneIdentity,
    reconstruct_lane_change_geometry,
)
from src.scenario.lane_change_observation_results import (
    LaneChangeObservationResultFactory,
)
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_lane_topology import CarlaLanePairTopology


@dataclass(slots=True)
class LaneChangeTracker(LaneChangeObservationResultFactory):
    request_id: str
    direction: LaneChangeDirection
    source_identity: LaneIdentity
    target_identity: LaneIdentity
    generation: int
    baseline_frame: int
    context: str
    lc_event_id: str | None
    topology: CarlaLanePairTopology | None = None
    last_frame: int | None = None
    movement_observed: bool = False
    target_entered: bool = False
    in_progress_recorded: bool = False
    unverified_reason: str | None = None
    submitted: bool = False
    submission_frame: int | None = None
    submission_host_monotonic_ns: int | None = None
    submission_source_lateral_m: float | None = None
    previous_vehicle_reference: PlanarPose | None = None
    boundary_outside_frame: int | None = None
    boundary_inside_frame: int | None = None

    @property
    def unverified(self) -> bool:
        return self.unverified_reason is not None

    def mark_submitted(
        self,
        timestamp: TimestampEnvelope,
        sample: ResearchSnapshotSample | None,
    ) -> LaneChangeObservationResult | None:
        self.submitted = True
        carla_timestamp = None if sample is None else sample.timestamp.carla_snapshot
        frame = None if carla_timestamp is None else carla_timestamp.frame
        self.submission_frame = frame
        self.submission_host_monotonic_ns = timestamp.host.monotonic_ns
        if sample is None:
            return None
        identities = (
            (self.source_identity, self.target_identity)
            if self.topology is None
            else self.topology.advance(sample.transform)
        )
        if identities is None:
            return self.mark_unverified("SUBMISSION_TOPOLOGY_UNAVAILABLE")
        source_identity, target_identity = identities
        try:
            geometry = reconstruct_lane_change_geometry(
                sample,
                self.direction,
                source_identity,
                target_identity,
            )
        except OverflowError:
            return None
        if geometry is None:
            return None
        source_context, target_context, _ = geometry
        self.submission_source_lateral_m = source_context.geometry.lateral_error_m
        self.previous_vehicle_reference = PlanarPose(
            source_context.vehicle_pose.x,
            source_context.vehicle_pose.y,
            source_context.waypoint_pose.yaw_rad,
        )
        if (
            abs(target_context.geometry.lateral_error_m)
            > target_context.lane_width_m / 2.0
        ):
            self.boundary_outside_frame = frame
        return None

    def mark_unverified(self, reason: str) -> LaneChangeObservationResult:
        return self._unverified(reason, None)

    def observe(
        self,
        sample: ResearchSnapshotSample,
        bounding_box: ActivationVehicleBoundingBox,
        config: AutomationInteractionConfig,
    ) -> LaneChangeObservationResult:
        if self.unverified:
            return self._result(
                LaneChangeObservationStatus.IGNORED, "ALREADY_UNVERIFIED"
            )
        if not self.submitted:
            return self._result(LaneChangeObservationStatus.IGNORED, "NOT_SUBMITTED")
        carla_timestamp = sample.timestamp.carla_snapshot
        if carla_timestamp is None:
            return self._unverified("MISSING_CARLA_FRAME", None)
        frame = carla_timestamp.frame
        previous = self.last_frame
        floor = max(
            self.baseline_frame,
            self.submission_frame or self.baseline_frame,
            self.baseline_frame if previous is None else previous,
        )
        if frame == floor:
            return self._result(
                LaneChangeObservationStatus.IGNORED, "DUPLICATE_FRAME", frame
            )
        if frame < floor:
            return self._unverified("FRAME_REGRESSION", frame)
        if (
            self.submission_host_monotonic_ns is None
            or carla_timestamp.host_capture_started_monotonic_ns
            <= self.submission_host_monotonic_ns
        ):
            return self._result(
                LaneChangeObservationStatus.IGNORED, "PRE_SUBMISSION_OBSERVATION"
            )
        self.last_frame = frame
        identities = (
            (self.source_identity, self.target_identity)
            if self.topology is None
            else self.topology.advance(sample.transform)
        )
        if identities is None:
            return self._unverified("GEOMETRY_CONTINUITY_UNAVAILABLE", frame, previous)
        source_identity, target_identity = identities
        try:
            geometry = reconstruct_lane_change_geometry(
                sample,
                self.direction,
                source_identity,
                target_identity,
            )
        except OverflowError:
            return self._unverified("GEOMETRY_NONFINITE", frame, previous)
        if geometry is None:
            return self._unverified("GEOMETRY_CONTINUITY_UNAVAILABLE", frame, previous)
        source_context, target_context, current_identity = geometry
        source_failure = lane_alignment_failure(source_context, bounding_box, config)
        target_failure = lane_alignment_failure(target_context, bounding_box, config)
        source_lateral = source_context.geometry.lateral_error_m
        if self.submission_source_lateral_m is None:
            return self._unverified("SUBMISSION_GEOMETRY_UNAVAILABLE", frame, previous)
        lateral_displacement = source_lateral - self.submission_source_lateral_m
        if self.previous_vehicle_reference is None:
            return self._unverified("SUBMISSION_POSE_UNAVAILABLE", frame, previous)
        vehicle_lateral_travel = compute_lane_relative_geometry(
            source_context.vehicle_pose,
            self.previous_vehicle_reference,
        ).lateral_error_m
        self.previous_vehicle_reference = PlanarPose(
            source_context.vehicle_pose.x,
            source_context.vehicle_pose.y,
            source_context.waypoint_pose.yaw_rad,
        )
        target_lateral = target_context.geometry.lateral_error_m
        requested_lateral_movement = (
            lateral_displacement < 0.0 and vehicle_lateral_travel < 0.0
            if self.direction is LaneChangeDirection.LEFT
            else lateral_displacement > 0.0 and vehicle_lateral_travel > 0.0
        )
        if requested_lateral_movement:
            self.movement_observed = True
        inside_target = abs(target_lateral) <= target_context.lane_width_m / 2.0
        entered_target_corridor = (
            inside_target
            and self.boundary_outside_frame is not None
            and self.boundary_inside_frame is None
        )
        if self.boundary_inside_frame is None:
            if inside_target:
                self.boundary_inside_frame = frame
            else:
                self.boundary_outside_frame = frame
        if current_identity == target_identity:
            self.target_entered = True
        status = LaneChangeObservationStatus.OBSERVED
        if (
            entered_target_corridor
            and self.movement_observed
            and not self.in_progress_recorded
        ):
            self.in_progress_recorded = True
            status = LaneChangeObservationStatus.IN_PROGRESS
        if (
            self.movement_observed
            and self.boundary_outside_frame is not None
            and self.boundary_inside_frame is not None
            and self.target_entered
            and current_identity == target_identity
            and source_failure is not None
            and target_failure is None
        ):
            status = LaneChangeObservationStatus.COMPLETED
        return LaneChangeObservationResult(
            status,
            None,
            previous,
            frame,
            None if previous is None else frame - previous,
            source_failure is None,
            target_failure is None,
            self.target_entered,
            current_identity,
            source_lateral,
            source_context.geometry.heading_error_rad,
            target_lateral,
            target_context.geometry.heading_error_rad,
            self.boundary_outside_frame,
            self.boundary_inside_frame,
            None if source_failure is None else source_failure.value,
            None if target_failure is None else target_failure.value,
            target_context.waypoint_pose.x,
            target_context.waypoint_pose.y,
            target_context.waypoint_pose.yaw_rad,
            target_context.lane_width_m,
            config.center_tolerance_m,
            config.heading_tolerance_rad,
        )
