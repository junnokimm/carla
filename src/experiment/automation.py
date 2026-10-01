from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never


class AutomationAvailability(StrEnum):
    """Whether the driving automation feature can currently be engaged."""

    UNAVAILABLE = "UNAVAILABLE"
    AVAILABLE = "AVAILABLE"


class DrivingControlMode(StrEnum):
    """Which actor currently controls the vehicle."""

    MANUAL = "MANUAL"
    NOA_ACTIVE = "NOA_ACTIVE"


class AutomationStateTransitionError(ValueError):
    """Raised when a transition would create an invalid automation state."""

    availability: AutomationAvailability
    control_mode: DrivingControlMode

    def __init__(
        self,
        availability: AutomationAvailability,
        control_mode: DrivingControlMode,
    ) -> None:
        self.availability = availability
        self.control_mode = control_mode
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"control mode {self.control_mode.value} requires automation to be "
            f"{AutomationAvailability.AVAILABLE.value}"
        )


@dataclass(frozen=True, slots=True)
class AutomationState:
    """Immutable automation availability and vehicle control state."""

    availability: AutomationAvailability
    control_mode: DrivingControlMode

    def __post_init__(self) -> None:
        match self.availability:
            case AutomationAvailability.AVAILABLE:
                return
            case AutomationAvailability.UNAVAILABLE:
                match self.control_mode:
                    case DrivingControlMode.MANUAL:
                        return
                    case DrivingControlMode.NOA_ACTIVE:
                        raise AutomationStateTransitionError(
                            self.availability, self.control_mode
                        )
                    case unreachable:
                        assert_never(unreachable)
            case unreachable:
                assert_never(unreachable)


def set_automation_availability(
    state: AutomationState,
    availability: AutomationAvailability,
) -> AutomationState:
    """Return the state produced by changing automation availability."""
    match state.availability, availability:
        case (
            AutomationAvailability.UNAVAILABLE,
            AutomationAvailability.UNAVAILABLE,
        ) | (AutomationAvailability.AVAILABLE, AutomationAvailability.AVAILABLE):
            return state
        case (
            AutomationAvailability.UNAVAILABLE,
            AutomationAvailability.AVAILABLE,
        ):
            return AutomationState(availability, DrivingControlMode.MANUAL)
        case (
            AutomationAvailability.AVAILABLE,
            AutomationAvailability.UNAVAILABLE,
        ):
            return AutomationState(availability, DrivingControlMode.MANUAL)
        case unreachable:
            assert_never(unreachable)


def set_driving_control_mode(
    state: AutomationState,
    control_mode: DrivingControlMode,
) -> AutomationState:
    """Return the state produced by changing the active control mode."""
    match state.control_mode, control_mode:
        case (DrivingControlMode.MANUAL, DrivingControlMode.MANUAL) | (
            DrivingControlMode.NOA_ACTIVE,
            DrivingControlMode.NOA_ACTIVE,
        ):
            return state
        case (DrivingControlMode.MANUAL, DrivingControlMode.NOA_ACTIVE) | (
            DrivingControlMode.NOA_ACTIVE,
            DrivingControlMode.MANUAL,
        ):
            return AutomationState(state.availability, control_mode)
        case unreachable:
            assert_never(unreachable)
