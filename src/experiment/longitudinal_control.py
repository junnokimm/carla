from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

type InvalidControlValue = float | tuple[float, float]


class LongitudinalControlValidationError(ValueError):
    """Describe an invalid longitudinal control input or configuration value."""

    field: str
    value: InvalidControlValue
    constraint: str

    def __init__(
        self,
        field: str,
        value: InvalidControlValue,
        constraint: str,
    ) -> None:
        self.field = field
        self.value = value
        self.constraint = constraint
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.field} must be {self.constraint}; received {self.value!r}"


def _require_nonnegative(field: str, value: float) -> None:
    if not isfinite(value) or value < 0.0:
        raise LongitudinalControlValidationError(field, value, "finite and >= 0")


def _require_positive(field: str, value: float) -> None:
    if not isfinite(value) or value <= 0.0:
        raise LongitudinalControlValidationError(field, value, "finite and > 0")


def _require_normalized_limit(field: str, value: float) -> None:
    if not isfinite(value) or value <= 0.0 or value > 1.0:
        raise LongitudinalControlValidationError(
            field, value, "finite and within (0, 1]"
        )


def _require_normalized_output(field: str, value: float) -> None:
    if not isfinite(value) or value < 0.0 or value > 1.0:
        raise LongitudinalControlValidationError(
            field, value, "finite and within [0, 1]"
        )


@dataclass(frozen=True, slots=True)
class LongitudinalControlConfig:
    """Explicit tuning for deterministic target-speed proportional control."""

    target_speed_kmh: float
    speed_deadband_kmh: float
    acceleration_gain: float
    braking_gain: float
    max_throttle: float
    max_brake: float

    def __post_init__(self) -> None:
        _require_nonnegative("target_speed_kmh", self.target_speed_kmh)
        _require_nonnegative("speed_deadband_kmh", self.speed_deadband_kmh)
        _require_positive("acceleration_gain", self.acceleration_gain)
        _require_positive("braking_gain", self.braking_gain)
        _require_normalized_limit("max_throttle", self.max_throttle)
        _require_normalized_limit("max_brake", self.max_brake)


@dataclass(frozen=True, slots=True)
class LongitudinalControlCommand:
    """Normalized mutually exclusive throttle and brake outputs."""

    throttle: float
    brake: float

    def __post_init__(self) -> None:
        _require_normalized_output("throttle", self.throttle)
        _require_normalized_output("brake", self.brake)
        if self.throttle > 0.0 and self.brake > 0.0:
            raise LongitudinalControlValidationError(
                "throttle_and_brake",
                (self.throttle, self.brake),
                "mutually exclusive",
            )


def compute_longitudinal_control(
    current_speed_kmh: float,
    config: LongitudinalControlConfig,
) -> LongitudinalControlCommand:
    """Compute a target-speed command without runtime side effects."""
    _require_nonnegative("current_speed_kmh", current_speed_kmh)
    speed_error = config.target_speed_kmh - current_speed_kmh

    if abs(speed_error) <= config.speed_deadband_kmh:
        return LongitudinalControlCommand(throttle=0.0, brake=0.0)
    if speed_error > 0.0:
        throttle = min(
            max(config.acceleration_gain * speed_error, 0.0),
            config.max_throttle,
        )
        return LongitudinalControlCommand(throttle=throttle, brake=0.0)

    brake = min(
        max(config.braking_gain * abs(speed_error), 0.0),
        config.max_brake,
    )
    return LongitudinalControlCommand(throttle=0.0, brake=brake)
