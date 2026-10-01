from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, isfinite, pi, sin


@dataclass(frozen=True, slots=True)
class LaneGeometryValidationError(ValueError):
    field: str
    value: float
    constraint: str

    def __str__(self) -> str:
        return f"{self.field} must be {self.constraint}; received {self.value!r}"


@dataclass(frozen=True, slots=True)
class PlanarPose:
    x: float
    y: float
    yaw_rad: float

    def __post_init__(self) -> None:
        _require_finite("x", self.x)
        _require_finite("y", self.y)
        _require_finite("yaw_rad", self.yaw_rad)


@dataclass(frozen=True, slots=True)
class LaneGeometryObservation:
    lateral_error_m: float
    heading_error_rad: float

    def __post_init__(self) -> None:
        _require_finite("lateral_error_m", self.lateral_error_m)
        _require_finite("heading_error_rad", self.heading_error_rad)
        if not -pi <= self.heading_error_rad <= pi:
            raise LaneGeometryValidationError(
                "heading_error_rad",
                self.heading_error_rad,
                "within [-pi, pi]",
            )


def compute_lane_relative_geometry(
    vehicle_pose: PlanarPose,
    lane_center_pose: PlanarPose,
) -> LaneGeometryObservation:
    """Return right-positive lateral and heading errors in the lane XY frame."""
    offset_x = vehicle_pose.x - lane_center_pose.x
    offset_y = vehicle_pose.y - lane_center_pose.y
    lateral_error_m = offset_x * -sin(lane_center_pose.yaw_rad) + offset_y * cos(
        lane_center_pose.yaw_rad
    )
    yaw_delta = vehicle_pose.yaw_rad - lane_center_pose.yaw_rad
    heading_error_rad = atan2(sin(yaw_delta), cos(yaw_delta))
    return LaneGeometryObservation(
        lateral_error_m=lateral_error_m,
        heading_error_rad=heading_error_rad,
    )


def _require_finite(field: str, value: float) -> None:
    if not isfinite(value):
        raise LaneGeometryValidationError(field, value, "finite")
