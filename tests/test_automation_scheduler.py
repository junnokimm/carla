from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_scheduler import AutomationControlScheduler
from src.experiment.noa_control import NoAControlCommand


class StepBackendError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FixedAutomationRuntime:
    state: AutomationState


class RecordingStepBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.call_count = 0
        self._fail = fail

    def step(self) -> NoAControlCommand:
        self.call_count += 1
        if self._fail:
            raise StepBackendError
        return NoAControlCommand(throttle=0.2, brake=0.0, steering=-0.1)


def test_manual_update_does_not_step_backend_or_change_runtime_state() -> None:
    initial_state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.MANUAL,
    )
    runtime = FixedAutomationRuntime(initial_state)
    backend = RecordingStepBackend()
    scheduler = AutomationControlScheduler(runtime, backend)

    control_applied = scheduler.update()

    assert control_applied is False
    assert backend.call_count == 0
    assert runtime.state is initial_state


def test_noa_active_update_steps_backend_once_without_changing_runtime_state() -> None:
    initial_state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )
    runtime = FixedAutomationRuntime(initial_state)
    backend = RecordingStepBackend()
    scheduler = AutomationControlScheduler(runtime, backend)

    control_applied = scheduler.update()

    assert control_applied is True
    assert backend.call_count == 1
    assert runtime.state is initial_state


def test_noa_active_update_propagates_backend_error() -> None:
    runtime = FixedAutomationRuntime(
        AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.NOA_ACTIVE,
        )
    )
    backend = RecordingStepBackend(fail=True)
    scheduler = AutomationControlScheduler(runtime, backend)

    with pytest.raises(StepBackendError):
        scheduler.update()

    assert backend.call_count == 1
