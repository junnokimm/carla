from __future__ import annotations

from typing import Protocol, assert_never

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
    set_automation_availability,
    set_driving_control_mode,
)


class AutomationControlBackend(Protocol):
    """Apply runtime transitions for the future research automation system."""

    def enter_manual_control(self) -> None: ...

    def enter_noa_control(self) -> None: ...


class AutomationRuntimeController:
    """Commit immutable automation state after runtime transitions succeed."""

    def __init__(
        self,
        initial_state: AutomationState,
        backend: AutomationControlBackend,
    ) -> None:
        self._state = initial_state
        self._backend = backend

    @property
    def state(self) -> AutomationState:
        """Return the latest successfully committed automation state."""
        return self._state

    def request_control_mode(
        self, control_mode: DrivingControlMode
    ) -> AutomationState:
        """Request a runtime control transition and commit it on success."""
        target_state = set_driving_control_mode(self._state, control_mode)
        return self._commit(target_state)

    def set_availability(
        self, availability: AutomationAvailability
    ) -> AutomationState:
        """Update availability, disengaging runtime control when required."""
        target_state = set_automation_availability(self._state, availability)
        return self._commit(target_state)

    def _commit(self, target_state: AutomationState) -> AutomationState:
        if target_state is self._state:
            return self._state

        match self._state.control_mode:
            case DrivingControlMode.MANUAL:
                match target_state.control_mode:
                    case DrivingControlMode.MANUAL:
                        pass
                    case DrivingControlMode.NOA_ACTIVE:
                        self._backend.enter_noa_control()
                    case unreachable:
                        assert_never(unreachable)
            case DrivingControlMode.NOA_ACTIVE:
                match target_state.control_mode:
                    case DrivingControlMode.MANUAL:
                        self._backend.enter_manual_control()
                    case DrivingControlMode.NOA_ACTIVE:
                        pass
                    case unreachable:
                        assert_never(unreachable)
            case unreachable:
                assert_never(unreachable)

        self._state = target_state
        return self._state
