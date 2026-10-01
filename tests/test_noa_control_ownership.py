from __future__ import annotations

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.noa_control import NoAControlCommand
from src.experiment.noa_runtime import SingleControlOwnershipBackend


class OwnershipTransitionError(RuntimeError):
    pass


class RecordingAutopilotVehicle:
    def __init__(self, operations: list[str], *, fail: bool = False) -> None:
        self.operations = operations
        self.fail = fail
        self.autopilot_enabled = True

    def set_autopilot(self, enabled: bool) -> None:
        self.operations.append(f"autopilot:{enabled}")
        if self.fail:
            raise OwnershipTransitionError
        self.autopilot_enabled = enabled


class RecordingCustomBackend:
    def __init__(
        self,
        operations: list[str],
        *,
        active: bool = False,
        fail_noa: bool = False,
        fail_manual: bool = False,
    ) -> None:
        self.operations = operations
        self.active = active
        self.fail_noa = fail_noa
        self.fail_manual = fail_manual

    def enter_noa_control(self) -> None:
        self.operations.append("custom:noa")
        if self.fail_noa:
            raise OwnershipTransitionError
        self.active = True

    def enter_manual_control(self) -> None:
        self.operations.append("custom:manual")
        if self.fail_manual:
            raise OwnershipTransitionError
        self.active = False

    def step(self) -> NoAControlCommand:
        self.operations.append("custom:step")
        return NoAControlCommand(0.0, 0.0, 0.0)


def make_runtime(
    mode: DrivingControlMode,
    vehicle: RecordingAutopilotVehicle,
    backend: RecordingCustomBackend,
) -> AutomationRuntimeController:
    ownership = SingleControlOwnershipBackend(vehicle, backend)
    return AutomationRuntimeController(
        AutomationState(AutomationAvailability.AVAILABLE, mode),
        ownership,
    )


def test_activation_disables_autopilot_before_custom_backend() -> None:
    operations: list[str] = []
    vehicle = RecordingAutopilotVehicle(operations)
    backend = RecordingCustomBackend(operations)
    runtime = make_runtime(DrivingControlMode.MANUAL, vehicle, backend)

    runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert operations == ["autopilot:False", "custom:noa"]
    assert vehicle.autopilot_enabled is False
    assert backend.active is True
    assert runtime.state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_deactivation_keeps_autopilot_off_before_custom_backend() -> None:
    operations: list[str] = []
    vehicle = RecordingAutopilotVehicle(operations)
    backend = RecordingCustomBackend(operations, active=True)
    runtime = make_runtime(DrivingControlMode.NOA_ACTIVE, vehicle, backend)

    runtime.request_control_mode(DrivingControlMode.MANUAL)

    assert operations == ["autopilot:False", "custom:manual"]
    assert vehicle.autopilot_enabled is False
    assert backend.active is False
    assert runtime.state.control_mode is DrivingControlMode.MANUAL


def test_autopilot_disable_failure_prevents_custom_activation_and_state_commit() -> (
    None
):
    operations: list[str] = []
    vehicle = RecordingAutopilotVehicle(operations, fail=True)
    backend = RecordingCustomBackend(operations)
    runtime = make_runtime(DrivingControlMode.MANUAL, vehicle, backend)

    with pytest.raises(OwnershipTransitionError):
        runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert operations == ["autopilot:False"]
    assert backend.active is False
    assert runtime.state.control_mode is DrivingControlMode.MANUAL


def test_custom_activation_failure_leaves_manual_state_and_autopilot_off() -> None:
    operations: list[str] = []
    vehicle = RecordingAutopilotVehicle(operations)
    backend = RecordingCustomBackend(operations, fail_noa=True)
    runtime = make_runtime(DrivingControlMode.MANUAL, vehicle, backend)

    with pytest.raises(OwnershipTransitionError):
        runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert operations == ["autopilot:False", "custom:noa"]
    assert vehicle.autopilot_enabled is False
    assert backend.active is False
    assert runtime.state.control_mode is DrivingControlMode.MANUAL


def test_custom_deactivation_failure_preserves_noa_state_and_autopilot_off() -> None:
    operations: list[str] = []
    vehicle = RecordingAutopilotVehicle(operations)
    backend = RecordingCustomBackend(operations, active=True, fail_manual=True)
    runtime = make_runtime(DrivingControlMode.NOA_ACTIVE, vehicle, backend)

    with pytest.raises(OwnershipTransitionError):
        runtime.request_control_mode(DrivingControlMode.MANUAL)

    assert operations == ["autopilot:False", "custom:manual"]
    assert vehicle.autopilot_enabled is False
    assert backend.active is True
    assert runtime.state.control_mode is DrivingControlMode.NOA_ACTIVE
