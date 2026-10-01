from __future__ import annotations

from dataclasses import dataclass
from typing import Final, assert_never

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.events import ResearchEventRecorder
from src.logging.csv_logger import ResearchEvent

AUTOMATION_ACTIVATED_EVENT: Final = ResearchEvent(event_type="automation_activated")
AUTOMATION_DEACTIVATED_EVENT: Final = ResearchEvent(event_type="automation_deactivated")


@dataclass(frozen=True, slots=True)
class ResearchAutomationRuntimeController:
    """Record committed automation mode changes through the research event path."""

    runtime: AutomationRuntimeController
    recorder: ResearchEventRecorder

    @property
    def state(self) -> AutomationState:
        return self.runtime.state

    def request_control_mode(
        self,
        control_mode: DrivingControlMode,
    ) -> AutomationState:
        previous_mode = self.state.control_mode
        state = self.runtime.request_control_mode(control_mode)
        self._record_mode_transition(previous_mode, state.control_mode)
        return state

    def set_availability(
        self,
        availability: AutomationAvailability,
    ) -> AutomationState:
        previous_mode = self.state.control_mode
        state = self.runtime.set_availability(availability)
        self._record_mode_transition(previous_mode, state.control_mode)
        return state

    def _record_mode_transition(
        self,
        previous_mode: DrivingControlMode,
        current_mode: DrivingControlMode,
    ) -> None:
        match previous_mode, current_mode:
            case (DrivingControlMode.MANUAL, DrivingControlMode.MANUAL) | (
                DrivingControlMode.NOA_ACTIVE,
                DrivingControlMode.NOA_ACTIVE,
            ):
                return
            case (DrivingControlMode.MANUAL, DrivingControlMode.NOA_ACTIVE):
                event = AUTOMATION_ACTIVATED_EVENT
            case (DrivingControlMode.NOA_ACTIVE, DrivingControlMode.MANUAL):
                event = AUTOMATION_DEACTIVATED_EVENT
            case unreachable:
                assert_never(unreachable)
        self.recorder.record_now(event)
