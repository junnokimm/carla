from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext


class ActivationFailureReason(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    GEOMETRY_UNAVAILABLE = "GEOMETRY_UNAVAILABLE"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    DRIVER_BRAKE_ACTIVE = "DRIVER_BRAKE_ACTIVE"
    NOT_CENTERED = "NOT_CENTERED"
    HEADING_MISALIGNED = "HEADING_MISALIGNED"
    VEHICLE_STRADDLING = "VEHICLE_STRADDLING"
    LANE_CHANGE_IN_PROGRESS = "LANE_CHANGE_IN_PROGRESS"


class DeactivationReason(StrEnum):
    DRIVER_BRAKE = "DRIVER_BRAKE"
    DRIVER_BUTTON = "DRIVER_BUTTON"
    AVAILABILITY_LOST = "AVAILABILITY_LOST"


class AutomationInteractionEventType(StrEnum):
    INITIAL_STATE = "INITIAL_STATE"
    REQUEST = "REQUEST"
    TRANSITION = "TRANSITION"
    FAILURE = "FAILURE"
    AVAILABILITY = "AVAILABILITY"
    DISENGAGEMENT = "DISENGAGEMENT"


@dataclass(frozen=True, slots=True)
class AutomationInteractionEvent:
    event_type: AutomationInteractionEventType
    state: AutomationState
    requested_mode: DrivingControlMode | None = None
    failure_reason: ActivationFailureReason | None = None
    deactivation_reason: DeactivationReason | None = None
    stage: str | None = None


@dataclass(frozen=True, slots=True)
class AutomationInteractionConfig:
    center_tolerance_m: float
    heading_tolerance_rad: float
    driver_brake_threshold: float
    button_deactivation_enabled: bool

    def __post_init__(self) -> None:
        values = (
            ("center_tolerance_m", self.center_tolerance_m),
            ("heading_tolerance_rad", self.heading_tolerance_rad),
            ("driver_brake_threshold", self.driver_brake_threshold),
        )
        for field, value in values:
            if not isfinite(value) or value < 0.0:
                raise AutomationInteractionConfigError(field, value)
        if self.driver_brake_threshold <= 0.0 or self.driver_brake_threshold > 1.0:
            raise AutomationInteractionConfigError(
                "driver_brake_threshold", self.driver_brake_threshold
            )


@dataclass(frozen=True, slots=True)
class AutomationInteractionConfigError(ValueError):
    field: str
    value: float

    def __str__(self) -> str:
        return f"{self.field} has invalid value {self.value!r}"


@dataclass(frozen=True, slots=True)
class DriverInput:
    throttle: float = 0.0
    brake: float = 0.0
    steering: float = 0.0
    steering_engaged: bool = False
    hand_brake: bool = False
    activation_requested: bool = False
    deactivation_requested: bool = False
    exit_confirm_requested: bool = False
    exit_reject_requested: bool = False
    lane_change_in_progress: bool = False
    stage: str | None = None

    def __post_init__(self) -> None:
        values = (
            ("throttle", self.throttle, 0.0, 1.0),
            ("brake", self.brake, 0.0, 1.0),
            ("steering", self.steering, -1.0, 1.0),
        )
        for field, value, lower, upper in values:
            if not isfinite(value) or value < lower or value > upper:
                raise DriverInputError(field, value, lower, upper)


class DriverInputError(ValueError):
    def __init__(self, field: str, value: float, lower: float, upper: float) -> None:
        self.field = field
        self.value = value
        self.lower = lower
        self.upper = upper
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.field} must be within [{self.lower}, {self.upper}]"


class AutomationInitializationError(RuntimeError):
    def __init__(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        failure_reason: ActivationFailureReason,
    ) -> None:
        self.module = module
        self.condition = condition
        self.failure_reason = failure_reason
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"initial {self.module.value} {self.condition.value} activation failed: "
            f"{self.failure_reason.value}"
        )


class SteeringOverrideTarget(Protocol):
    def set_driver_steering(self, steering: float | None) -> None: ...


class ActivationVehicleExtent(Protocol):
    x: float
    y: float


class ActivationVehicleBoundingBox(Protocol):
    extent: ActivationVehicleExtent
    location: ActivationVehicleExtent
    rotation: ActivationVehicleRotation


class ActivationVehicleRotation(Protocol):
    yaw: float


class ActivationVehicle(Protocol):
    @property
    def bounding_box(self) -> ActivationVehicleBoundingBox: ...


class ActivationGeometry(Protocol):
    def observe_with_context(
        self, vehicle: ActivationVehicle
    ) -> CarlaLaneGeometryContext: ...


@dataclass(frozen=True, slots=True)
class AutomationInteractionConditionError(ValueError):
    module: ExperimentModule
    condition: ExperimentCondition

    def __str__(self) -> str:
        return f"condition {self.condition.value} is invalid for {self.module.value}"
