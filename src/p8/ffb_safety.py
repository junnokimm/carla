"""Deterministic Offline FFB safety simulation. No command authorizes hardware."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from src.p8.ffb_model import (
    ZERO,
    FfbContext,
    FfbObservation,
    FfbParameters,
    finite_number,
)


class FfbState(StrEnum):
    DISABLED = "DISABLED"
    EVALUATING = "OFFLINE_EVALUATING"
    FAULT = "FAULT"
    CLOSED = "CLOSED"


class FfbRequest(StrEnum):
    HOLD = "HOLD"
    ENABLE_EVALUATION = "ENABLE_OFFLINE_EVALUATION"
    DISABLE = "DISABLE"
    ACKNOWLEDGE_FAULT = "ACKNOWLEDGE_FAULT"


@dataclass(frozen=True, slots=True)
class FfbLimits:
    """Provisional numerical limits only; no Nm/driver-unit mapping."""

    max_spring: float = 0.10
    max_damper: float = 0.10
    rise_per_second: float = 0.10
    fall_per_second: float = 0.20
    wheel_max_age_s: float = 0.15
    vehicle_max_age_s: float = 0.50
    max_update_gap_s: float = 0.25

    def __post_init__(self) -> None:
        for value in (self.max_spring, self.max_damper):
            if not finite_number(value) or not 0 <= value <= 1:
                raise ValueError("strength limits must be within [0, 1]")
        for value in (
            self.rise_per_second,
            self.fall_per_second,
            self.wheel_max_age_s,
            self.vehicle_max_age_s,
            self.max_update_gap_s,
        ):
            if not finite_number(value) or value <= 0:
                raise ValueError("rates and time bounds must be finite and positive")


DEFAULT_LIMITS = FfbLimits()


@dataclass(frozen=True, slots=True)
class FfbCommand:
    sequence: int
    context: FfbContext | None
    issued_at: float
    valid_until: float
    state: FfbState
    reason: str
    parameters: FfbParameters
    decay_preview: FfbParameters

    @property
    def physical_output_allowed(self) -> Literal[False]:
        return False

    @property
    def simulation_allowed(self) -> bool:
        return self.state is FfbState.EVALUATING

    @property
    def disable_target(self) -> FfbParameters:
        return ZERO


class FfbSafety:
    def __init__(self, limits: FfbLimits = DEFAULT_LIMITS) -> None:
        self.limits = limits
        self.state = FfbState.DISABLED
        self.reason = "DEFAULT_DISABLED"
        self._sequence = 0
        self._context: FfbContext | None = None
        self._mode: str | None = None
        self._last_at: float | None = None
        self._wheel_at: float | None = None
        self._vehicle_at: float | None = None
        self._limited = ZERO

    def step(
        self,
        observation: FfbObservation | None,
        target: FfbParameters | None,
        *,
        now: float,
        request: FfbRequest = FfbRequest.HOLD,
    ) -> FfbCommand:
        if self.state is FfbState.CLOSED:
            return self._command(self._last_at or 0.0)
        if not finite_number(now) or now < 0:
            return self.fault("INVALID_CLOCK", now=now)
        if self._last_at is not None and now < self._last_at:
            return self.fault("CLOCK_REGRESSION", now=now)
        dt = 0.0 if self._last_at is None else now - self._last_at
        self._last_at = now
        if not isinstance(request, FfbRequest):
            return self.fault("INVALID_REQUEST", now=now)
        if self.state is FfbState.EVALUATING and dt > self.limits.max_update_gap_s:
            return self.fault("UPDATE_GAP", now=now)
        if observation is None:
            self._disable("INPUT_MISSING", clear=True)
            return self._command(now)
        problem = self._validate_observation(observation, now)
        if problem is not None:
            return self.fault(problem, now=now)
        boundary = (
            "CONTEXT_CHANGED"
            if self._context is not None and observation.context != self._context
            else "MODE_CHANGED"
            if self._mode is not None and observation.mode != self._mode
            else None
        )
        if boundary is None and (
            (
                self._wheel_at is not None
                and observation.wheel_observed_at < self._wheel_at
            )
            or (
                self._vehicle_at is not None
                and observation.vehicle_observed_at < self._vehicle_at
            )
        ):
            return self.fault("OBSERVATION_REGRESSION", now=now)
        self._context, self._mode = observation.context, observation.mode
        self._wheel_at = observation.wheel_observed_at
        self._vehicle_at = observation.vehicle_observed_at
        if self.state is FfbState.FAULT:
            if (
                request is FfbRequest.ACKNOWLEDGE_FAULT
                and observation.session_active
                and observation.stage_active
            ):
                self.state, self.reason = FfbState.DISABLED, "FAULT_ACKNOWLEDGED"
            return self._command(now)  # Acknowledgment is never activation.
        if boundary is not None:
            self._disable(boundary, clear=True)
            return self._command(
                now
            )  # Even ENABLE in this call cannot cross a boundary.
        if not observation.session_active:
            self._disable("SESSION_INACTIVE", clear=True)
            return self._command(now)
        if not observation.stage_active:
            self._disable("STAGE_INACTIVE", clear=True)
            return self._command(now)
        if observation.mode != "MANUAL":
            self._disable("NOA_POLICY_UNAPPROVED", clear=True)
            return self._command(now)
        if target is None:
            self._disable("TARGET_MISSING", clear=True)
            return self._command(now)
        if (
            not isinstance(target, FfbParameters)
            or not all(
                finite_number(value) and 0 <= value <= 1
                for value in (target.spring, target.damper)
            )
            or not finite_number(target.center)
            or target.center != 0
        ):
            return self.fault("INVALID_TARGET", now=now)
        if request is FfbRequest.DISABLE:
            self._disable("USER_DISABLED", clear=False)
        elif (
            request is FfbRequest.ENABLE_EVALUATION and self.state is FfbState.DISABLED
        ):
            self.state, self.reason = FfbState.EVALUATING, "OFFLINE_ONLY"
            self._limited, dt = ZERO, 0.0
        if self.state is FfbState.EVALUATING:
            desired = FfbParameters(
                min(target.spring, self.limits.max_spring),
                min(target.damper, self.limits.max_damper),
            )
        else:
            desired = ZERO
        self._limited = FfbParameters(
            self._slew(self._limited.spring, desired.spring, dt),
            self._slew(self._limited.damper, desired.damper, dt),
        )
        valid_until = min(
            now + self.limits.max_update_gap_s,
            observation.wheel_observed_at + self.limits.wheel_max_age_s,
            observation.vehicle_observed_at + self.limits.vehicle_max_age_s,
        )
        if not finite_number(valid_until):
            return self.fault("INVALID_DEADLINE", now=now)
        return self._command(now, valid_until)

    def _validate_observation(self, sample: FfbObservation, now: float) -> str | None:
        if not isinstance(sample, FfbObservation) or not isinstance(
            sample.context, FfbContext
        ):
            return "INVALID_OBSERVATION"
        if not all(
            isinstance(v, str) and v.strip()
            for v in (
                sample.context.session_id,
                sample.context.stage_id,
                sample.context.actor_id,
                sample.context.device_generation,
            )
        ):
            return "INVALID_CONTEXT"
        if not all(
            type(value) is bool
            for value in (sample.connected, sample.session_active, sample.stage_active)
        ):
            return "INVALID_STATUS"
        if not sample.connected:
            return "DEVICE_DISCONNECTED"
        if sample.mode not in ("MANUAL", "NOA_ACTIVE"):
            return "INVALID_MODE"
        if (
            not finite_number(sample.wheel_position)
            or not -1 <= sample.wheel_position <= 1
        ):
            return "INVALID_WHEEL"
        if not finite_number(sample.speed_kmh) or sample.speed_kmh < 0:
            return "INVALID_SPEED"
        for name, stamp, max_age in (
            ("WHEEL", sample.wheel_observed_at, self.limits.wheel_max_age_s),
            ("VEHICLE", sample.vehicle_observed_at, self.limits.vehicle_max_age_s),
        ):
            if not finite_number(stamp) or stamp < 0:
                return f"{name}_TIMESTAMP_INVALID"
            if stamp > now:
                return f"{name}_TIMESTAMP_FUTURE"
            if now - stamp > max_age:
                return f"{name}_STALE"
        return None

    def _slew(self, current: float, desired: float, dt: float) -> float:
        if desired > current:
            return min(desired, current + self.limits.rise_per_second * dt)
        return max(desired, current - self.limits.fall_per_second * dt)

    def _disable(self, reason: str, *, clear: bool) -> None:
        if self.state is not FfbState.FAULT:
            self.state, self.reason = FfbState.DISABLED, reason
        if clear:
            self._limited = ZERO

    def fault(self, reason: str, *, now: float) -> FfbCommand:
        if self.state not in (FfbState.FAULT, FfbState.CLOSED):
            self.state, self.reason = FfbState.FAULT, str(reason)[:160]
        self._limited = ZERO
        stamp = self._last_at or 0.0
        if finite_number(now) and now >= stamp:
            stamp = now
            self._last_at = now
        return self._command(stamp)

    def close(self, *, now: float) -> FfbCommand:
        self.state, self.reason, self._limited = FfbState.CLOSED, "CLOSED", ZERO
        return self.fault("CLOSED", now=now)

    def _command(self, now: float, valid_until: float | None = None) -> FfbCommand:
        self._sequence += 1
        allowed = self.state is FfbState.EVALUATING
        return FfbCommand(
            self._sequence,
            self._context,
            now,
            valid_until if allowed and valid_until is not None else now,
            self.state,
            self.reason,
            self._limited if allowed else ZERO,
            self._limited,
        )
