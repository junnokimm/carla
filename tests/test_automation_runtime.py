from __future__ import annotations

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    AutomationStateTransitionError,
    DrivingControlMode,
)
from src.experiment.automation_runtime import AutomationRuntimeController


class BackendTransitionError(RuntimeError):
    pass


class RecordingAutomationBackend:
    def __init__(
        self,
        *,
        fail_manual: bool = False,
        fail_noa: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.fail_manual = fail_manual
        self.fail_noa = fail_noa

    def enter_manual_control(self) -> None:
        self.calls.append("manual")
        if self.fail_manual:
            raise BackendTransitionError

    def enter_noa_control(self) -> None:
        self.calls.append("noa")
        if self.fail_noa:
            raise BackendTransitionError


def make_state(
    availability: AutomationAvailability,
    control_mode: DrivingControlMode,
) -> AutomationState:
    return AutomationState(availability, control_mode)


def test_initialization_preserves_state_without_calling_backend() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend()

    controller = AutomationRuntimeController(initial, backend)

    assert controller.state is initial
    assert backend.calls == []


def test_manual_to_noa_calls_backend_before_committing_state() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    result = controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert backend.calls == ["noa"]
    assert result is controller.state
    assert controller.state == make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )


def test_noa_to_manual_calls_backend_before_committing_state() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    controller.request_control_mode(DrivingControlMode.MANUAL)

    assert backend.calls == ["manual"]
    assert controller.state == make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )


def test_repeated_manual_request_preserves_state_without_backend_call() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    result = controller.request_control_mode(DrivingControlMode.MANUAL)

    assert result is initial
    assert controller.state is initial
    assert backend.calls == []


def test_repeated_noa_request_preserves_state_without_backend_call() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    result = controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert result is initial
    assert controller.state is initial
    assert backend.calls == []


def test_unavailable_noa_request_is_rejected_before_backend_call() -> None:
    initial = make_state(
        AutomationAvailability.UNAVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    with pytest.raises(AutomationStateTransitionError):
        controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert controller.state is initial
    assert backend.calls == []


def test_noa_backend_failure_preserves_manual_state() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend(fail_noa=True)
    controller = AutomationRuntimeController(initial, backend)

    with pytest.raises(BackendTransitionError):
        controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert controller.state is initial
    assert backend.calls == ["noa"]


def test_manual_backend_failure_preserves_noa_state() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )
    backend = RecordingAutomationBackend(fail_manual=True)
    controller = AutomationRuntimeController(initial, backend)

    with pytest.raises(BackendTransitionError):
        controller.request_control_mode(DrivingControlMode.MANUAL)

    assert controller.state is initial
    assert backend.calls == ["manual"]


def test_manual_availability_loss_commits_without_backend_call() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    controller.set_availability(AutomationAvailability.UNAVAILABLE)

    assert controller.state == make_state(
        AutomationAvailability.UNAVAILABLE, DrivingControlMode.MANUAL
    )
    assert backend.calls == []


def test_availability_gain_commits_without_backend_call() -> None:
    initial = make_state(
        AutomationAvailability.UNAVAILABLE, DrivingControlMode.MANUAL
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    controller.set_availability(AutomationAvailability.AVAILABLE)

    assert controller.state == make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    assert backend.calls == []


def test_active_availability_loss_enters_manual_before_committing() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )
    backend = RecordingAutomationBackend()
    controller = AutomationRuntimeController(initial, backend)

    controller.set_availability(AutomationAvailability.UNAVAILABLE)

    assert backend.calls == ["manual"]
    assert controller.state == make_state(
        AutomationAvailability.UNAVAILABLE, DrivingControlMode.MANUAL
    )


def test_failed_active_availability_loss_preserves_available_noa_state() -> None:
    initial = make_state(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )
    backend = RecordingAutomationBackend(fail_manual=True)
    controller = AutomationRuntimeController(initial, backend)

    with pytest.raises(BackendTransitionError):
        controller.set_availability(AutomationAvailability.UNAVAILABLE)

    assert controller.state is initial
    assert backend.calls == ["manual"]
