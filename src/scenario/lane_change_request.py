from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite, radians

from src.experiment.automation_activation_gate import lane_alignment_failure
from src.experiment.automation_interaction_types import (
    ActivationVehicleBoundingBox,
    AutomationInteractionConfig,
)
from src.experiment.lane_change import LaneChangeDirection, LaneChangeEligibility
from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.scenario.lane_change_lifecycle import LaneChangeTracker
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext
from src.vehicle.carla_lane_topology import CarlaLanePairTopology


@dataclass(frozen=True, slots=True)
class LaneChangeRequestBuild:
    tracker: LaneChangeTracker | None
    reason: str | None


def build_lane_change_tracker(
    request_id: str,
    direction: LaneChangeDirection,
    eligibility: LaneChangeEligibility,
    baseline: ResearchSnapshotSample | None,
    generation: int,
    context: str,
    lc_event_id: str | None,
    bounding_box: ActivationVehicleBoundingBox,
    config: AutomationInteractionConfig,
    topology: CarlaLanePairTopology | None = None,
) -> LaneChangeRequestBuild:
    if not eligibility.eligible:
        return LaneChangeRequestBuild(None, eligibility.reason)
    if (
        eligibility.current_road_id is None
        or eligibility.current_section_id is None
        or eligibility.current_lane_id is None
        or eligibility.target_road_id is None
        or eligibility.target_section_id is None
        or eligibility.target_lane_id is None
    ):
        return LaneChangeRequestBuild(None, "STATIC_IDENTITY_INCOMPLETE")
    if baseline is None or baseline.timestamp.carla_snapshot is None:
        return LaneChangeRequestBuild(None, "SOURCE_BASELINE_FRAME_UNAVAILABLE")
    source_identity = (
        eligibility.current_road_id,
        eligibility.current_section_id,
        eligibility.current_lane_id,
    )
    target_identity = (
        eligibility.target_road_id,
        eligibility.target_section_id,
        eligibility.target_lane_id,
    )
    if topology is not None and topology.identities != (
        source_identity,
        target_identity,
    ):
        return LaneChangeRequestBuild(None, "TOPOLOGY_BASELINE_IDENTITY_MISMATCH")
    if (
        baseline.road_id,
        baseline.section_id,
        baseline.lane_id,
    ) != source_identity:
        return LaneChangeRequestBuild(None, "SOURCE_BASELINE_IDENTITY_MISMATCH")
    if baseline.lane_transform is None or baseline.lane_width_m is None:
        return LaneChangeRequestBuild(None, "SOURCE_BASELINE_GEOMETRY_UNAVAILABLE")
    if direction is LaneChangeDirection.LEFT:
        adjacent_id = baseline.left_lane_id
        adjacent_transform = baseline.left_lane_transform
        adjacent_width = baseline.left_lane_width_m
        adjacent_identity = (
            baseline.left_road_id,
            baseline.left_section_id,
            baseline.left_lane_id,
        )
    else:
        adjacent_id = baseline.right_lane_id
        adjacent_transform = baseline.right_lane_transform
        adjacent_width = baseline.right_lane_width_m
        adjacent_identity = (
            baseline.right_road_id,
            baseline.right_section_id,
            baseline.right_lane_id,
        )
    if (
        adjacent_id != target_identity[2]
        or adjacent_transform is None
        or adjacent_width is None
    ):
        return LaneChangeRequestBuild(None, "TARGET_BASELINE_GEOMETRY_UNAVAILABLE")
    if adjacent_identity != target_identity:
        return LaneChangeRequestBuild(None, "TARGET_BASELINE_IDENTITY_MISMATCH")
    source_location = baseline.lane_transform.location
    target_location = adjacent_transform.location
    values = (
        float(baseline.lane_width_m),
        float(adjacent_width),
        float(baseline.transform.location.x),
        float(baseline.transform.location.y),
        float(baseline.transform.rotation.yaw),
        float(source_location.x),
        float(source_location.y),
        float(baseline.lane_transform.rotation.yaw),
        float(target_location.x),
        float(target_location.y),
        float(adjacent_transform.rotation.yaw),
    )
    if not all(isfinite(value) for value in values) or min(values[:2]) <= 0.0:
        return LaneChangeRequestBuild(None, "BASELINE_GEOMETRY_NONFINITE")
    vehicle_pose = PlanarPose(values[2], values[3], radians(values[4]))
    source_pose = PlanarPose(values[5], values[6], radians(values[7]))
    target_pose = PlanarPose(values[8], values[9], radians(values[10]))
    source_context = CarlaLaneGeometryContext(
        compute_lane_relative_geometry(vehicle_pose, source_pose),
        *source_identity,
        values[0],
        vehicle_pose,
        source_pose,
    )
    if lane_alignment_failure(source_context, bounding_box, config) is not None:
        return LaneChangeRequestBuild(None, "SOURCE_BASELINE_MISALIGNED")
    target_lateral = compute_lane_relative_geometry(
        vehicle_pose, target_pose
    ).lateral_error_m
    separation = hypot(values[8] - values[5], values[9] - values[6])
    if separation <= 0.0:
        return LaneChangeRequestBuild(None, "SOURCE_TARGET_GEOMETRY_NOT_SEPARATED")
    target_side = compute_lane_relative_geometry(
        target_pose, source_pose
    ).lateral_error_m
    if (direction is LaneChangeDirection.LEFT and target_side >= 0.0) or (
        direction is LaneChangeDirection.RIGHT and target_side <= 0.0
    ):
        return LaneChangeRequestBuild(None, "TARGET_BASELINE_WRONG_SIDE")
    if abs(target_lateral) <= values[1] / 2.0:
        return LaneChangeRequestBuild(None, "SOURCE_BASELINE_INSIDE_TARGET")
    return LaneChangeRequestBuild(
        LaneChangeTracker(
            request_id,
            direction,
            source_identity,
            target_identity,
            generation,
            baseline.timestamp.carla_snapshot.frame,
            context,
            lc_event_id,
            topology,
        ),
        None,
    )
