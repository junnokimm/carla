"""Offline dispatch contract only. No physical backend is supplied or registered.

A future physical backend MUST reject these commands: physical_output_allowed is
always False. Driver units/direction, effect lifetime/stop/ownership/watchdog and
crash cleanup require a separately reviewed, hardware-validated implementation.
"""

from __future__ import annotations

from typing import Protocol

from src.p8.ffb_model import (
    DEFAULT_CURVE,
    FfbContext,
    FfbCurve,
    FfbObservation,
    calculate_parameters,
    finite_number,
)
from src.p8.ffb_safety import (
    DEFAULT_LIMITS,
    FfbCommand,
    FfbLimits,
    FfbRequest,
    FfbSafety,
    FfbState,
)


class FfbOutput(Protocol):
    """Fake implementations record only. Never interpret simulation permission as torque permission."""

    def apply(self, command: FfbCommand, *, now: float) -> None: ...
    def disable(self, command: FfbCommand) -> None:
        """Invalidate earlier sequences even when a stop attempt fails."""
        ...

    def record_fault(self, reason: str) -> None: ...
    def close(self) -> None: ...


def validate_simulated_command(
    command: FfbCommand,
    *,
    now: float,
    context: FfbContext,
    previous_sequence: int,
    limits: FfbLimits = DEFAULT_LIMITS,
) -> None:
    """Receiver-side replay/expiry check, for Fake use only. No hardware attestation."""
    if (
        not command.simulation_allowed
        or command.physical_output_allowed
        or command.context != context
        or type(command.sequence) is not int
        or command.sequence <= previous_sequence
        or not finite_number(now)
        or not finite_number(command.issued_at)
        or not finite_number(command.valid_until)
        or command.issued_at < 0
        or not command.issued_at <= now <= command.valid_until
        or command.valid_until - command.issued_at > limits.max_update_gap_s
        or not finite_number(command.parameters.spring)
        or not 0 <= command.parameters.spring <= limits.max_spring
        or not finite_number(command.parameters.damper)
        or not 0 <= command.parameters.damper <= limits.max_damper
        or not finite_number(command.parameters.center)
        or command.parameters.center != 0
    ):
        raise ValueError("invalid, replayed or expired Offline command")


class OfflineFfbDispatcher:
    """Explicitly constructed in tests only; retains bounded last-error diagnostics."""

    def __init__(
        self,
        output: FfbOutput,
        *,
        safety: FfbSafety | None = None,
        curve: FfbCurve = DEFAULT_CURVE,
    ) -> None:
        self.output = output
        self.safety = safety if safety is not None else FfbSafety()
        self.curve = curve
        self.last_backend_error: str | None = None
        self.backend_failure_count = 0
        self._closed = False

    def step(
        self,
        observation: FfbObservation | None,
        *,
        now: float,
        request: FfbRequest = FfbRequest.HOLD,
    ) -> FfbCommand:
        if self._closed:
            return self.safety.close(now=now)
        try:
            target = None
            if observation is not None:
                try:
                    target = calculate_parameters(observation, self.curve)
                except ValueError:
                    pass  # Safety identifies the invalid original input, not a fabricated zero.
            command = self.safety.step(observation, target, now=now, request=request)
        except Exception as error:  # noqa: BLE001 -- deterministic fail-closed simulation boundary
            command = self.safety.fault(f"MODEL_ERROR:{type(error).__name__}", now=now)
        try:
            if command.simulation_allowed:
                self.output.apply(command, now=now)
            else:
                self.output.disable(command)
                if command.state is FfbState.FAULT:
                    self.output.record_fault(command.reason)
        except Exception as error:  # noqa: BLE001 -- backend failure invalidates evaluation permission
            self._record_error(error)
            command = self.safety.fault("BACKEND_FAILURE", now=now)
            # Failure recording must not prevent a disable attempt.
            for action in (
                lambda: self.output.record_fault(command.reason),
                lambda: self.output.disable(command),
            ):
                try:
                    action()
                except Exception as secondary:  # noqa: BLE001 -- retain bounded diagnostics
                    self._record_error(secondary)
        return command

    def close(self, *, now: float) -> FfbCommand:
        command = self.safety.close(now=now)
        if not self._closed:
            self._closed = True
            for action in (lambda: self.output.disable(command), self.output.close):
                try:
                    action()
                except Exception as error:  # noqa: BLE001 -- attempt all cleanup, never hide primary exception
                    self._record_error(error)
        return command

    def _record_error(self, error: Exception) -> None:
        self.backend_failure_count += 1
        self.last_backend_error = f"{type(error).__name__}:{error}"[:256]
