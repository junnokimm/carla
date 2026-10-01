from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, assert_never

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.noa_control import NoAControlCommand


class AutomationRuntimeStateSource(Protocol):
    """Expose the canonical committed automation state."""

    @property
    def state(self) -> AutomationState: ...


class OneTickNoAControlBackend(Protocol):
    """Apply one active NoA control tick."""

    def step(self) -> NoAControlCommand: ...


@dataclass(frozen=True, slots=True)
class AutomationControlScheduler:
    """Schedule at most one NoA backend step for one runtime iteration."""

    runtime: AutomationRuntimeStateSource
    backend: OneTickNoAControlBackend

    def update(self) -> bool:
        """Return whether one NoA control tick claimed this iteration."""
        match self.runtime.state.control_mode:
            case DrivingControlMode.MANUAL:
                return False
            case DrivingControlMode.NOA_ACTIVE:
                self.backend.step()
                return True
            case unreachable:
                assert_never(unreachable)
