"""Device-independent, experimental condition-effect coefficients; never torque.

No CARLA, Pygame, device API, clock reads or runtime integration. Wheel position
is the existing calibrated/deadzoned [-1, 1] input, NOT degrees or radians.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


def finite_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True, slots=True)
class FfbContext:
    session_id: str
    stage_id: str
    actor_id: str
    device_generation: str  # New connection must have a new adapter generation.


@dataclass(frozen=True, slots=True)
class FfbObservation:
    context: FfbContext
    wheel_position: float | None
    wheel_observed_at: float | None  # Original host monotonic seconds.
    speed_kmh: float | None
    vehicle_observed_at: float | None  # Do not replace with delivery/update time.
    mode: str = "MANUAL"
    connected: bool = True
    session_active: bool = True
    stage_active: bool = True


@dataclass(frozen=True, slots=True)
class FfbParameters:
    spring: float = 0.0
    damper: float = 0.0
    center: float = 0.0


ZERO = FfbParameters()


@dataclass(frozen=True, slots=True)
class FfbCurve:
    """Offline demonstration values, NOT approved TS-XW or NoA strength settings."""

    reference_speed_kmh: float = 100.0
    spring_at_rest: float = 0.06
    spring_at_reference: float = 0.04
    damper_at_rest: float = 0.025
    damper_at_reference: float = 0.05

    def __post_init__(self) -> None:
        if not finite_number(self.reference_speed_kmh) or self.reference_speed_kmh <= 0:
            raise ValueError("reference speed must be finite and positive")
        coefficients = (
            self.spring_at_rest,
            self.spring_at_reference,
            self.damper_at_rest,
            self.damper_at_reference,
        )
        if not all(finite_number(v) and 0 <= v <= 1 for v in coefficients):
            raise ValueError("abstract coefficients must be within [0, 1]")


DEFAULT_CURVE = FfbCurve()


def calculate_parameters(
    observation: FfbObservation, curve: FfbCurve = DEFAULT_CURVE
) -> FfbParameters:
    """Calculate coefficients, not displacement force or steering-velocity torque.

    Spring describes restoration toward a fixed zero center; damper describes
    resistance to movement. Their future device effects evaluate displacement and
    velocity separately. Temporal/identity/permission checks belong to safety.
    """
    position, speed = observation.wheel_position, observation.speed_kmh
    if not finite_number(position) or not -1 <= position <= 1:
        raise ValueError("wheel_position must be normalized within [-1, 1]")
    if not finite_number(speed) or speed < 0:
        raise ValueError("speed_kmh must be finite and nonnegative")
    blend = min(speed / curve.reference_speed_kmh, 1.0)
    return FfbParameters(
        spring=(1 - blend) * curve.spring_at_rest + blend * curve.spring_at_reference,
        damper=(1 - blend) * curve.damper_at_rest + blend * curve.damper_at_reference,
        center=0.0,  # Never follow NoA target steering or move the physical center.
    )
