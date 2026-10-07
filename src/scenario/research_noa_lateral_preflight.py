from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, isfinite, radians, sin
from typing import TYPE_CHECKING, Final, Protocol

import carla

from src.experiment.lane_geometry import (
    LaneGeometryValidationError,
    PlanarPose,
    compute_lane_relative_geometry,
)
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryAdapter,
    CarlaLaneGeometryContext,
    CarlaLaneGeometryUnavailableError,
    CarlaLocation,
    CarlaWaypoint,
)
from src.vehicle.carla_noa_control import CarlaVelocity
from src.vehicle.speed import calculate_speed_kmh

if TYPE_CHECKING:
    from src.scenario.research_noa_lateral_validation import (
        LateralValidationInitialCondition,
    )

MAX_STATIONARY_SPEED_KMH: Final = 0.05
PLACEMENT_POSITION_TOLERANCE_M: Final = 0.02
PLACEMENT_HEADING_TOLERANCE_RAD: Final = 0.005


class LateralValidationBoundingBox(Protocol):
    def get_world_vertices(
        self, transform: carla.Transform
    ) -> list[carla.Location]: ...


class LateralValidationVehicle(Protocol):
    @property
    def bounding_box(self) -> LateralValidationBoundingBox: ...

    def get_transform(self) -> carla.Transform: ...

    def get_velocity(self) -> CarlaVelocity: ...


class LateralValidationMap(Protocol):
    def get_waypoint(
        self,
        location: CarlaLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> CarlaWaypoint | None: ...


@dataclass(frozen=True, slots=True)
class LaneIdentity:
    road_id: int
    section_id: int
    lane_id: int


@dataclass(frozen=True, slots=True)
class LateralValidationPlacement:
    expected_transform: carla.Transform
    condition: LateralValidationInitialCondition
    original_lane_identity: LaneIdentity


@dataclass(frozen=True, slots=True)
class LateralValidationInitialState:
    condition: LateralValidationInitialCondition
    geometry: CarlaLaneGeometryContext
    speed_kmh: float
    minimum_lane_margin_m: float


class LateralValidationPreflightError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class LateralValidationVehicleMovingError(LateralValidationPreflightError):
    def __init__(self, speed_kmh: float) -> None:
        self.speed_kmh = speed_kmh
        super().__init__("vehicle must be stationary before NoA activation")


def _lane_identity(
    road_id: int,
    section_id: int,
    lane_id: int,
) -> LaneIdentity:
    return LaneIdentity(int(road_id), int(section_id), int(lane_id))


def _require_finite(description: str, *values: float) -> None:
    if not all(isfinite(value) for value in values):
        raise LateralValidationPreflightError(f"{description} must be finite")


def validate_stationary_speed(vehicle: LateralValidationVehicle) -> float:
    velocity = vehicle.get_velocity()
    speed_kmh = calculate_speed_kmh(velocity.x, velocity.y, velocity.z)
    _require_finite("vehicle speed", speed_kmh)
    if speed_kmh > MAX_STATIONARY_SPEED_KMH:
        raise LateralValidationVehicleMovingError(speed_kmh)
    return speed_kmh


def validate_initial_state(
    vehicle: LateralValidationVehicle,
    world_map: LateralValidationMap,
    placement: LateralValidationPlacement,
) -> LateralValidationInitialState:
    actual = vehicle.get_transform()
    expected = placement.expected_transform
    position_error = hypot(
        actual.location.x - expected.location.x,
        actual.location.y - expected.location.y,
    )
    raw_heading_error = radians(actual.rotation.yaw - expected.rotation.yaw)
    heading_error = atan2(sin(raw_heading_error), cos(raw_heading_error))
    _require_finite("vehicle placement", position_error, heading_error)
    if (
        position_error > PLACEMENT_POSITION_TOLERANCE_M
        or abs(heading_error) > PLACEMENT_HEADING_TOLERANCE_RAD
    ):
        raise LateralValidationPreflightError(
            "vehicle did not remain at requested stationary placement"
        )

    speed_kmh = validate_stationary_speed(vehicle)

    try:
        geometry = CarlaLaneGeometryAdapter(world_map).observe_with_context(vehicle)
    except CarlaLaneGeometryUnavailableError as error:
        raise LateralValidationPreflightError(
            "center lane geometry is unavailable"
        ) from error
    except LaneGeometryValidationError as error:
        raise LateralValidationPreflightError(
            "center lane geometry must be finite"
        ) from error
    _require_finite(
        "center lane geometry",
        geometry.lane_width_m,
        geometry.geometry.lateral_error_m,
        geometry.geometry.heading_error_rad,
        geometry.vehicle_pose.x,
        geometry.vehicle_pose.y,
        geometry.vehicle_pose.yaw_rad,
        geometry.waypoint_pose.x,
        geometry.waypoint_pose.y,
        geometry.waypoint_pose.yaw_rad,
    )
    center_identity = _lane_identity(
        geometry.road_id,
        geometry.section_id,
        geometry.lane_id,
    )
    if center_identity != placement.original_lane_identity:
        raise LateralValidationPreflightError(
            "vehicle center is not within the original initial lane"
        )

    margins: list[float] = []
    for corner in vehicle.bounding_box.get_world_vertices(actual):
        corner_waypoint = world_map.get_waypoint(
            corner,
            project_to_road=False,
            lane_type=carla.LaneType.Driving,
        )
        if corner_waypoint is None:
            raise LateralValidationPreflightError(
                "vehicle footprint is not wholly within the original initial lane"
            )
        corner_identity = _lane_identity(
            corner_waypoint.road_id,
            corner_waypoint.section_id,
            corner_waypoint.lane_id,
        )
        if corner_identity != placement.original_lane_identity:
            raise LateralValidationPreflightError(
                "vehicle footprint is not wholly within the original initial lane"
            )
        lane_transform = corner_waypoint.transform
        corner_x = float(corner.x)
        corner_y = float(corner.y)
        waypoint_x = float(lane_transform.location.x)
        waypoint_y = float(lane_transform.location.y)
        waypoint_yaw = radians(float(lane_transform.rotation.yaw))
        lane_width_m = float(corner_waypoint.lane_width)
        _require_finite(
            "corner-local lane geometry and margin",
            lane_width_m,
            corner_x,
            corner_y,
            waypoint_x,
            waypoint_y,
            waypoint_yaw,
        )
        corner_pose = PlanarPose(
            corner_x,
            corner_y,
            waypoint_yaw,
        )
        waypoint_pose = PlanarPose(
            waypoint_x,
            waypoint_y,
            waypoint_yaw,
        )
        corner_geometry = compute_lane_relative_geometry(corner_pose, waypoint_pose)
        margin = lane_width_m / 2.0 - abs(corner_geometry.lateral_error_m)
        _require_finite(
            "corner-local lane geometry and margin",
            lane_width_m,
            corner_pose.x,
            corner_pose.y,
            corner_pose.yaw_rad,
            waypoint_pose.x,
            waypoint_pose.y,
            waypoint_pose.yaw_rad,
            corner_geometry.lateral_error_m,
            corner_geometry.heading_error_rad,
            margin,
        )
        if margin < 0.0:
            raise LateralValidationPreflightError(
                "vehicle footprint exceeds the lane margin"
            )
        margins.append(margin)

    if not margins:
        raise LateralValidationPreflightError("vehicle footprint has no corners")
    return LateralValidationInitialState(
        condition=placement.condition,
        geometry=geometry,
        speed_kmh=speed_kmh,
        minimum_lane_margin_m=min(margins),
    )
