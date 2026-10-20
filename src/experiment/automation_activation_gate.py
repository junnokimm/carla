from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, isfinite, radians, sin
from typing import Protocol

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    ActivationGeometry,
    ActivationVehicle,
    ActivationVehicleBoundingBox,
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.lane_geometry import LaneGeometryValidationError
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryContext,
    CarlaLaneGeometryUnavailableError,
)


class ActivationDiagnostics(Protocol):
    def timestamp(self) -> float: ...

    def record_operation_duration(
        self, operation: str, duration_seconds: float
    ) -> None: ...

    def set_control_mode(self, mode: DrivingControlMode) -> None: ...


@dataclass(frozen=True, slots=True)
class AutomationActivationGate:
    geometry: ActivationGeometry
    vehicle: ActivationVehicle
    config: AutomationInteractionConfig
    diagnostics: ActivationDiagnostics | None = None
    cache_static_bounds: bool = False
    _cached_bounds: ActivationVehicleBoundingBox | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def failure(
        self, driver_input: DriverInput, *, reuse_static_bounds: bool = False
    ) -> ActivationFailureReason | None:
        if driver_input.brake >= self.config.driver_brake_threshold:
            return ActivationFailureReason.DRIVER_BRAKE_ACTIVE
        if driver_input.lane_change_in_progress:
            return ActivationFailureReason.LANE_CHANGE_IN_PROGRESS
        diagnostics = self.diagnostics
        started = 0.0 if diagnostics is None else diagnostics.timestamp()
        try:
            context = self.geometry.observe_with_context(self.vehicle)
        except CarlaLaneGeometryUnavailableError:
            return ActivationFailureReason.GEOMETRY_UNAVAILABLE
        except LaneGeometryValidationError:
            return ActivationFailureReason.INVALID_GEOMETRY
        finally:
            if diagnostics is not None:
                diagnostics.record_operation_duration(
                    "activation_geometry_query", diagnostics.timestamp() - started
                )
        bounding_box = (
            self._cached_bounds
            if reuse_static_bounds and self.cache_static_bounds
            else None
        )
        if bounding_box is None:
            started = 0.0 if diagnostics is None else diagnostics.timestamp()
            # A failed refresh must never leave a previous successful box available.
            object.__setattr__(self, "_cached_bounds", None)
            try:
                bounding_box = self.vehicle.bounding_box
            finally:
                if diagnostics is not None:
                    diagnostics.record_operation_duration(
                        "activation_bounding_box_getter",
                        diagnostics.timestamp() - started,
                    )
            if self.cache_static_bounds:
                object.__setattr__(self, "_cached_bounds", bounding_box)
        else:
            if diagnostics is not None:
                diagnostics.record_operation_duration(
                    "activation_bounding_box_reused", 0.0
                )
        return lane_alignment_failure(
            context,
            bounding_box,
            self.config,
            require_centered=self.config.require_centered_activation,
        )


def lane_alignment_failure(
    context: CarlaLaneGeometryContext,
    bounding_box: ActivationVehicleBoundingBox,
    config: AutomationInteractionConfig,
    *,
    require_centered: bool = True,
) -> ActivationFailureReason | None:
    # Activation can use full-lane containment. Lane-change request/completion
    # callers retain strict alignment by default, even with the same policy.
    geometry = context.geometry
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
        geometry.lateral_error_m + location.x * sin(heading) + location.y * cos(heading)
    )
    lateral_half_extent = (
        abs(sin(bounding_box_heading)) * extent.x
        + abs(cos(bounding_box_heading)) * extent.y
    )
    if abs(bounding_box_lateral) + lateral_half_extent > context.lane_width_m / 2.0:
        return ActivationFailureReason.VEHICLE_STRADDLING
    if require_centered and abs(geometry.lateral_error_m) > config.center_tolerance_m:
        return ActivationFailureReason.NOT_CENTERED
    if abs(heading) > config.heading_tolerance_rad:
        return ActivationFailureReason.HEADING_MISALIGNED
    return None
