from dataclasses import FrozenInstanceError

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    AutomationStateTransitionError,
    DrivingControlMode,
    set_automation_availability,
    set_driving_control_mode,
)


def test_available_automation_can_start_in_manual_control() -> None:
    state = AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )

    assert state.availability is AutomationAvailability.AVAILABLE
    assert state.control_mode is DrivingControlMode.MANUAL


def test_available_automation_can_start_with_noa_active() -> None:
    state = AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.NOA_ACTIVE,
    )

    assert state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_unavailable_automation_can_start_in_manual_control() -> None:
    state = AutomationState(
        availability=AutomationAvailability.UNAVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )

    assert state.availability is AutomationAvailability.UNAVAILABLE


def test_unavailable_automation_rejects_noa_active_control() -> None:
    with pytest.raises(AutomationStateTransitionError):
        AutomationState(
            availability=AutomationAvailability.UNAVAILABLE,
            control_mode=DrivingControlMode.NOA_ACTIVE,
        )


def test_automation_state_is_immutable() -> None:
    state = AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )

    with pytest.raises(FrozenInstanceError):
        state.control_mode = DrivingControlMode.NOA_ACTIVE


def test_activate_noa_returns_new_state_without_mutating_input() -> None:
    initial = AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )

    active = set_driving_control_mode(initial, DrivingControlMode.NOA_ACTIVE)

    assert active == AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.NOA_ACTIVE,
    )
    assert initial.control_mode is DrivingControlMode.MANUAL


def test_activate_noa_rejects_unavailable_automation() -> None:
    unavailable = AutomationState(
        availability=AutomationAvailability.UNAVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )

    with pytest.raises(AutomationStateTransitionError):
        set_driving_control_mode(unavailable, DrivingControlMode.NOA_ACTIVE)


def test_automation_becoming_unavailable_returns_to_manual_control() -> None:
    active = AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.NOA_ACTIVE,
    )

    unavailable = set_automation_availability(
        active, AutomationAvailability.UNAVAILABLE
    )

    assert unavailable == AutomationState(
        availability=AutomationAvailability.UNAVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )


def test_repeated_transitions_return_the_same_state_object() -> None:
    state = AutomationState(
        availability=AutomationAvailability.AVAILABLE,
        control_mode=DrivingControlMode.MANUAL,
    )

    same_availability = set_automation_availability(
        state, AutomationAvailability.AVAILABLE
    )
    same_control_mode = set_driving_control_mode(state, DrivingControlMode.MANUAL)

    assert same_availability is state
    assert same_control_mode is state
