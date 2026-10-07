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
    """Explicit tuning for bounded target-speed PI control."""

    target_speed_kmh: float
    speed_deadband_kmh: float
    acceleration_gain: float
    braking_gain: float
    max_throttle: float
    max_brake: float
    integral_gain: float = 0.0

    def __post_init__(self) -> None:
        _require_nonnegative("target_speed_kmh", self.target_speed_kmh)
        _require_nonnegative("speed_deadband_kmh", self.speed_deadband_kmh)
        _require_positive("acceleration_gain", self.acceleration_gain)
        _require_positive("braking_gain", self.braking_gain)
        _require_normalized_limit("max_throttle", self.max_throttle)
        _require_normalized_limit("max_brake", self.max_brake)
        _require_nonnegative("integral_gain", self.integral_gain)


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


class LongitudinalController:
    """Accumulate bounded integral effort while active to reject speed droop."""

    __slots__ = (
        "_config",
        "_control_time_seconds",
        "_delta_seconds",
        "_integral_effort",
    )

    def __init__(self, config: LongitudinalControlConfig) -> None:
        self._config = config
        self._integral_effort = 0.0
        self._control_time_seconds: float | None = None
        self._delta_seconds = 0.0

    @property
    def integral_effort(self) -> float:
        return self._integral_effort

    @property
    def delta_seconds(self) -> float:
        return self._delta_seconds

    def reset(self) -> None:
        self._integral_effort = 0.0
        self._control_time_seconds = None
        self._delta_seconds = 0.0

    def compute(
        self,
        current_speed_kmh: float,
        *,
        control_time_seconds: float,
    ) -> LongitudinalControlCommand:
        """Advance one time-based PI step and return bounded actuator effort."""
        _require_nonnegative("current_speed_kmh", current_speed_kmh)
        _require_nonnegative("control_time_seconds", control_time_seconds)
        if (
            self._control_time_seconds is not None
            and control_time_seconds < self._control_time_seconds
        ):
            raise LongitudinalControlValidationError(
                "control_time_seconds",
                (self._control_time_seconds, control_time_seconds),
                "monotonic",
            )

        delta_seconds = (
            0.0
            if self._control_time_seconds is None
            else control_time_seconds - self._control_time_seconds
        )
        self._delta_seconds = delta_seconds
        speed_error = self._config.target_speed_kmh - current_speed_kmh
        if self._config.integral_gain > 0.0:
            proportional_effort = self._config.acceleration_gain * max(
                speed_error - self._config.speed_deadband_kmh,
                0.0,
            ) + self._config.braking_gain * min(
                speed_error + self._config.speed_deadband_kmh,
                0.0,
            )
        else:
            proportional_effort = _proportional_effort(speed_error, self._config)
        integral_candidate = min(
            max(
                self._integral_effort
                + self._config.integral_gain * speed_error * delta_seconds,
                -self._config.max_brake,
            ),
            self._config.max_throttle,
        )
        candidate_effort = proportional_effort + integral_candidate
        pushes_above_throttle_limit = (
            candidate_effort > self._config.max_throttle and speed_error > 0.0
        )
        pushes_below_brake_limit = (
            candidate_effort < -self._config.max_brake and speed_error < 0.0
        )
        if not pushes_above_throttle_limit and not pushes_below_brake_limit:
            self._integral_effort = integral_candidate
        self._control_time_seconds = control_time_seconds
        return _command_from_effort(
            proportional_effort + self._integral_effort,
            self._config,
        )


def compute_longitudinal_control(
    current_speed_kmh: float,
    config: LongitudinalControlConfig,
) -> LongitudinalControlCommand:
    """Compute a target-speed command without runtime side effects."""
    _require_nonnegative("current_speed_kmh", current_speed_kmh)
    speed_error = config.target_speed_kmh - current_speed_kmh
    return _command_from_effort(_proportional_effort(speed_error, config), config)


def _proportional_effort(
    speed_error: float,
    config: LongitudinalControlConfig,
) -> float:
    if abs(speed_error) <= config.speed_deadband_kmh:
        return 0.0
    if speed_error > 0.0:
        return config.acceleration_gain * speed_error
    return config.braking_gain * speed_error


def _command_from_effort(
    effort: float,
    config: LongitudinalControlConfig,
) -> LongitudinalControlCommand:
    bounded_effort = min(
        max(effort, -config.max_brake),
        config.max_throttle,
    )
    if bounded_effort >= 0.0:
        return LongitudinalControlCommand(throttle=bounded_effort, brake=0.0)
    return LongitudinalControlCommand(throttle=0.0, brake=-bounded_effort)
