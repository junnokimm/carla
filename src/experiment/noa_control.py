from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig, compute_lateral_control
from src.experiment.longitudinal_control import (
    LongitudinalControlConfig,
    compute_longitudinal_control,
)

type InvalidNoAControlValue = float | tuple[float, float]


@dataclass(frozen=True, slots=True)
class NoAControlValidationError(ValueError):
    """Describe an invalid normalized combined NoA command."""

    field: str
    value: InvalidNoAControlValue
    constraint: str

    def __str__(self) -> str:
        return f"{self.field} must be {self.constraint}; received {self.value!r}"


@dataclass(frozen=True, slots=True)
class NoAControlCommand:
    """Normalized throttle, brake, and steering for one NoA control tick."""

    throttle: float
    brake: float
    steering: float

    def __post_init__(self) -> None:
        _require_normalized("throttle", self.throttle, minimum=0.0)
        _require_normalized("brake", self.brake, minimum=0.0)
        _require_normalized("steering", self.steering, minimum=-1.0)
        if self.throttle > 0.0 and self.brake > 0.0:
            raise NoAControlValidationError(
                "throttle_and_brake",
                (self.throttle, self.brake),
                "mutually exclusive",
            )


def compute_noa_control(
    current_speed_kmh: float,
    lane_geometry: LaneGeometryObservation,
    longitudinal_config: LongitudinalControlConfig,
    lateral_config: LateralControlConfig,
) -> NoAControlCommand:
    """Combine the existing pure longitudinal and lateral controllers."""
    longitudinal = compute_longitudinal_control(
        current_speed_kmh,
        longitudinal_config,
    )
    lateral = compute_lateral_control(
        lane_geometry.lateral_error_m,
        lane_geometry.heading_error_rad,
        lateral_config,
    )
    return NoAControlCommand(
        throttle=longitudinal.throttle,
        brake=longitudinal.brake,
        steering=lateral.steering,
    )


def _require_normalized(field: str, value: float, *, minimum: float) -> None:
    if not isfinite(value) or not minimum <= value <= 1.0:
        raise NoAControlValidationError(
            field,
            value,
            f"finite and within [{minimum}, 1]",
        )
