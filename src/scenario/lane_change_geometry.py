from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import hypot, isfinite, radians

from src.experiment.lane_change import LaneChangeDirection
from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext, CarlaTransform

LaneIdentity = tuple[int, int, int]
LaneGeometryPair = tuple[
    CarlaLaneGeometryContext,
    CarlaLaneGeometryContext,
    LaneIdentity,
]


class LaneChangeObservationStatus(StrEnum):
    IGNORED = "IGNORED"
    OBSERVED = "OBSERVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class LaneChangeObservationResult:
    status: LaneChangeObservationStatus
    reason: str | None
    previous_frame: int | None
    current_frame: int | None
    frame_gap: int | None
    source_aligned: bool | None
    target_aligned: bool | None
    target_entered: bool
    observed_identity: LaneIdentity | None
    source_lateral_error_m: float | None
    source_heading_error_rad: float | None
    target_lateral_error_m: float | None
    target_heading_error_rad: float | None
    boundary_outside_frame: int | None
    boundary_inside_frame: int | None
    source_alignment_failure: str | None = None
    target_alignment_failure: str | None = None
    target_lane_center_x: float | None = None
    target_lane_center_y: float | None = None
    target_lane_heading_rad: float | None = None
    target_lane_width_m: float | None = None
    center_tolerance_m: float | None = None
    heading_tolerance_rad: float | None = None


def reconstruct_lane_change_geometry(
    sample: ResearchSnapshotSample,
    direction: LaneChangeDirection,
    source_identity: LaneIdentity,
    target_identity: LaneIdentity,
) -> LaneGeometryPair | None:
    if (
        sample.road_id is None
        or sample.section_id is None
        or sample.lane_id is None
        or sample.lane_width_m is None
        or sample.lane_transform is None
    ):
        return None
    current = (sample.road_id, sample.section_id, sample.lane_id)
    if current == source_identity:
        source_transform = sample.lane_transform
        source_width = sample.lane_width_m
        if direction is LaneChangeDirection.LEFT:
            neighbor_identity = (
                sample.left_road_id,
                sample.left_section_id,
                sample.left_lane_id,
            )
            target_width = sample.left_lane_width_m
            target_transform = sample.left_lane_transform
        else:
            neighbor_identity = (
                sample.right_road_id,
                sample.right_section_id,
                sample.right_lane_id,
            )
            target_width = sample.right_lane_width_m
            target_transform = sample.right_lane_transform
        if neighbor_identity != target_identity:
            return None
    elif current == target_identity:
        target_transform = sample.lane_transform
        target_width = sample.lane_width_m
        if direction is LaneChangeDirection.LEFT:
            neighbor_identity = (
                sample.right_road_id,
                sample.right_section_id,
                sample.right_lane_id,
            )
            source_width = sample.right_lane_width_m
            source_transform = sample.right_lane_transform
        else:
            neighbor_identity = (
                sample.left_road_id,
                sample.left_section_id,
                sample.left_lane_id,
            )
            source_width = sample.left_lane_width_m
            source_transform = sample.left_lane_transform
        if neighbor_identity != source_identity:
            return None
    else:
        return None
    if (
        source_transform is None
        or source_width is None
        or target_transform is None
        or target_width is None
    ):
        return None
    values = (
        float(source_width),
        float(target_width),
        float(source_transform.location.x),
        float(source_transform.location.y),
        float(source_transform.rotation.yaw),
        float(target_transform.location.x),
        float(target_transform.location.y),
        float(target_transform.rotation.yaw),
        float(sample.transform.location.x),
        float(sample.transform.location.y),
        float(sample.transform.rotation.yaw),
    )
    if not all(isfinite(value) for value in values) or min(values[:2]) <= 0.0:
        raise OverflowError
    separation = hypot(values[5] - values[2], values[6] - values[3])
    if separation <= 0.0:
        return None
    source_pose = _pose(source_transform)
    target_pose = _pose(target_transform)
    target_side = compute_lane_relative_geometry(target_pose, source_pose).lateral_error_m
    if (direction is LaneChangeDirection.LEFT and target_side >= 0.0) or (
        direction is LaneChangeDirection.RIGHT and target_side <= 0.0
    ):
        return None
    vehicle_pose = _pose(sample.transform)
    return (
        _context(vehicle_pose, source_transform, source_width, source_identity),
        _context(vehicle_pose, target_transform, target_width, target_identity),
        current,
    )


def _pose(transform: CarlaTransform) -> PlanarPose:
    return PlanarPose(
        float(transform.location.x),
        float(transform.location.y),
        radians(float(transform.rotation.yaw)),
    )


def _context(
    vehicle_pose: PlanarPose,
    lane_transform: CarlaTransform,
    lane_width: float,
    identity: LaneIdentity,
) -> CarlaLaneGeometryContext:
    lane_pose = _pose(lane_transform)
    return CarlaLaneGeometryContext(
        compute_lane_relative_geometry(vehicle_pose, lane_pose),
        identity[0],
        identity[1],
        identity[2],
        lane_width,
        vehicle_pose,
        lane_pose,
    )
