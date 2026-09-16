from __future__ import annotations

from enum import StrEnum
from typing import Protocol, assert_never


class AutopilotVehicle(Protocol):
    """Expose the CARLA autopilot control used by the driving-mode controller."""

    def set_autopilot(self, enabled: bool) -> None: ...


class DrivingMode(StrEnum):
    MANUAL = "MANUAL"
    AUTONOMOUS = "AUTONOMOUS"


class DrivingModeController:
    """Keep one vehicle's CARLA autopilot state aligned with its driving mode."""

    def __init__(
        self,
        vehicle: AutopilotVehicle,
        initial_mode: DrivingMode = DrivingMode.AUTONOMOUS,
    ) -> None:
        self._vehicle = vehicle
        self._mode = initial_mode
        match initial_mode:
            case DrivingMode.MANUAL:
                self._vehicle.set_autopilot(False)
            case DrivingMode.AUTONOMOUS:
                self._vehicle.set_autopilot(True)
            case unreachable:
                assert_never(unreachable)

    @property
    def mode(self) -> DrivingMode:
        """Return the active driving mode."""
        return self._mode

    def toggle(self) -> DrivingMode:
        """Switch mode and apply the matching CARLA autopilot setting."""
        match self._mode:
            case DrivingMode.MANUAL:
                self._mode = DrivingMode.AUTONOMOUS
                self._vehicle.set_autopilot(True)
            case DrivingMode.AUTONOMOUS:
                self._mode = DrivingMode.MANUAL
                self._vehicle.set_autopilot(False)
            case unreachable:
                assert_never(unreachable)
        return self._mode
