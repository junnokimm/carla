from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, pi


class LateralControlValidationError(ValueError):
    def __init__(self, field: str, value: float, requirement: str) -> None:
        self.field = field
        self.value = value
        self.requirement = requirement
        super().__init__(f"{field} must {requirement}, got {value!r}")


@dataclass(frozen=True)
class LateralControlConfig:
    lateral_error_gain: float
    heading_error_gain: float
    lateral_deadband_m: float
    heading_deadband_rad: float
    max_steering: float

    def __post_init__(self) -> None:
        _require_positive("lateral_error_gain", self.lateral_error_gain)
        _require_positive("heading_error_gain", self.heading_error_gain)
        _require_nonnegative("lateral_deadband_m", self.lateral_deadband_m)
        _require_nonnegative("heading_deadband_rad", self.heading_deadband_rad)
        _require_finite("max_steering", self.max_steering)
        if not 0.0 < self.max_steering <= 1.0:
            raise LateralControlValidationError(
                "max_steering", self.max_steering, "be in (0.0, 1.0]"
            )


@dataclass(frozen=True)
class LateralControlCommand:
    steering: float

    def __post_init__(self) -> None:
        _require_finite("steering", self.steering)
        if not -1.0 <= self.steering <= 1.0:
            raise LateralControlValidationError(
                "steering", self.steering, "be in [-1.0, 1.0]"
            )


def compute_lateral_control(
    lateral_error_m: float,
    heading_error_rad: float,
    config: LateralControlConfig,
) -> LateralControlCommand:
    """Compute normalized steering from signed minimal lane errors.

    Positive errors mean the vehicle is right of the lane target or heading
    right of the lane direction. Positive steering means steering right, so
    correction applies the opposite sign. Heading error must be in [-pi, pi].
    """
    _require_finite("lateral_error_m", lateral_error_m)
    _require_finite("heading_error_rad", heading_error_rad)
    if not -pi <= heading_error_rad <= pi:
        raise LateralControlValidationError(
            "heading_error_rad", heading_error_rad, "be in [-pi, pi]"
        )

    effective_lateral_error = (
        0.0 if abs(lateral_error_m) <= config.lateral_deadband_m else lateral_error_m
    )
    effective_heading_error = (
        0.0
        if abs(heading_error_rad) <= config.heading_deadband_rad
        else heading_error_rad
    )
    raw_steering = -(
        config.lateral_error_gain * effective_lateral_error
        + config.heading_error_gain * effective_heading_error
    )
    steering = max(-config.max_steering, min(config.max_steering, raw_steering))
    return LateralControlCommand(steering=steering)


def _require_finite(field: str, value: float) -> None:
    if not isfinite(value):
        raise LateralControlValidationError(field, value, "be finite")


def _require_positive(field: str, value: float) -> None:
    _require_finite(field, value)
    if value <= 0.0:
        raise LateralControlValidationError(field, value, "be greater than 0.0")


def _require_nonnegative(field: str, value: float) -> None:
    _require_finite(field, value)
    if value < 0.0:
        raise LateralControlValidationError(field, value, "be at least 0.0")
