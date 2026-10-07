from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import cos, degrees, isfinite, radians, sin
from typing import assert_never

import carla


class LateralValidationCase(StrEnum):
    BASELINE = "baseline"
    POSITION_LEFT = "position-left"
    POSITION_RIGHT = "position-right"
    HEADING_LEFT = "heading-left"
    HEADING_RIGHT = "heading-right"


@dataclass(frozen=True, slots=True)
class LateralValidationInitialCondition:
    case: LateralValidationCase
    position_offset_m: float
    heading_offset_rad: float

    def __post_init__(self) -> None:
        if not isfinite(self.position_offset_m) or not isfinite(
            self.heading_offset_rad
        ):
            raise LateralValidationConditionError(self)
        match self.case:
            case LateralValidationCase.BASELINE:
                valid = self.position_offset_m == 0.0 and self.heading_offset_rad == 0.0
            case LateralValidationCase.POSITION_LEFT:
                valid = self.position_offset_m < 0.0 and self.heading_offset_rad == 0.0
            case LateralValidationCase.POSITION_RIGHT:
                valid = self.position_offset_m > 0.0 and self.heading_offset_rad == 0.0
            case LateralValidationCase.HEADING_LEFT:
                valid = self.position_offset_m == 0.0 and self.heading_offset_rad < 0.0
            case LateralValidationCase.HEADING_RIGHT:
                valid = self.position_offset_m == 0.0 and self.heading_offset_rad > 0.0
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise LateralValidationConditionError(self)


class LateralValidationConditionError(ValueError):
    def __init__(self, condition: LateralValidationInitialCondition) -> None:
        self.condition = condition
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"offsets do not match lateral validation case {self.condition.case.value}"
        )


def apply_initial_condition(
    base: carla.Transform,
    condition: LateralValidationInitialCondition,
) -> carla.Transform:
    yaw_rad = radians(base.rotation.yaw)
    offset = condition.position_offset_m
    return carla.Transform(
        carla.Location(
            x=base.location.x - sin(yaw_rad) * offset,
            y=base.location.y + cos(yaw_rad) * offset,
            z=base.location.z,
        ),
        carla.Rotation(
            pitch=base.rotation.pitch,
            yaw=base.rotation.yaw + degrees(condition.heading_offset_rad),
            roll=base.rotation.roll,
        ),
    )


from src.scenario.research_noa_lateral_preflight import (
    LaneIdentity,
    LateralValidationInitialState,
    LateralValidationPlacement,
    LateralValidationPreflightError,
    LateralValidationVehicleMovingError,
    validate_initial_state,
    validate_stationary_speed,
)

__all__ = [
    "LaneIdentity",
    "LateralValidationCase",
    "LateralValidationConditionError",
    "LateralValidationInitialCondition",
    "LateralValidationInitialState",
    "LateralValidationPlacement",
    "LateralValidationPreflightError",
    "LateralValidationVehicleMovingError",
    "apply_initial_condition",
    "validate_initial_state",
    "validate_stationary_speed",
]
