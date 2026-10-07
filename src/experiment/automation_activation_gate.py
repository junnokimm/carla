from __future__ import annotations

from dataclasses import dataclass
from math import cos, isfinite, radians, sin

from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    ActivationGeometry,
    ActivationVehicle,
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.lane_geometry import LaneGeometryValidationError
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryUnavailableError


@dataclass(frozen=True, slots=True)
class AutomationActivationGate:
    geometry: ActivationGeometry
    vehicle: ActivationVehicle
    config: AutomationInteractionConfig

    def failure(self, driver_input: DriverInput) -> ActivationFailureReason | None:
        if driver_input.brake >= self.config.driver_brake_threshold:
            return ActivationFailureReason.DRIVER_BRAKE_ACTIVE
        if driver_input.lane_change_in_progress:
            return ActivationFailureReason.LANE_CHANGE_IN_PROGRESS
        try:
            context = self.geometry.observe_with_context(self.vehicle)
        except CarlaLaneGeometryUnavailableError:
            return ActivationFailureReason.GEOMETRY_UNAVAILABLE
        except LaneGeometryValidationError:
            return ActivationFailureReason.INVALID_GEOMETRY
        geometry = context.geometry
        bounding_box = self.vehicle.bounding_box
        extent = bounding_box.extent
        location = bounding_box.location
        rotation = bounding_box.rotation
        values = (
            context.lane_width_m,
            geometry.lateral_error_m,
            geometry.heading_error_rad,
            extent.x,
            extent.y,
            location.x,
            location.y,
            rotation.yaw,
        )
        if not all(isfinite(value) for value in values):
            return ActivationFailureReason.INVALID_GEOMETRY
        if context.lane_width_m <= 0.0 or extent.x < 0.0 or extent.y < 0.0:
            return ActivationFailureReason.INVALID_GEOMETRY
        heading = geometry.heading_error_rad
        bounding_box_heading = heading + radians(rotation.yaw)
        bounding_box_lateral = (
            geometry.lateral_error_m
            + location.x * sin(heading)
            + location.y * cos(heading)
        )
        lateral_half_extent = (
            abs(sin(bounding_box_heading)) * extent.x
            + abs(cos(bounding_box_heading)) * extent.y
        )
        if abs(bounding_box_lateral) + lateral_half_extent > context.lane_width_m / 2.0:
            return ActivationFailureReason.VEHICLE_STRADDLING
        if abs(geometry.lateral_error_m) > self.config.center_tolerance_m:
            return ActivationFailureReason.NOT_CENTERED
        if abs(heading) > self.config.heading_tolerance_rad:
            return ActivationFailureReason.HEADING_MISALIGNED
        return None
