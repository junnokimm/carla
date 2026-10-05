from __future__ import annotations

from math import isfinite
from typing import Final, Protocol

import carla

from src.experiment.automation_scheduler import (
    AutomationControlScheduler,
    AutomationRuntimeStateSource,
)
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.vehicle.carla_noa_control import CarlaVelocity
from src.vehicle.speed import calculate_speed_kmh

RESEARCH_LIVE_SMOKE_MAX_ACTUAL_SPEED_KMH: Final = 20.0


class _StepBackend(Protocol):
    def step(self) -> NoAControlCommand: ...


class _SpeedSource(Protocol):
    def get_velocity(self) -> CarlaVelocity: ...


class _VehicleDiagnosticsSource(_SpeedSource, Protocol):
    def get_control(self) -> carla.VehicleControl: ...


class ResearchNoAActualSpeedSafetyError(RuntimeError):
    def __init__(
        self,
        actual_speed_kmh: float,
        hard_limit_kmh: float = RESEARCH_LIVE_SMOKE_MAX_ACTUAL_SPEED_KMH,
    ) -> None:
        self.actual_speed_kmh = actual_speed_kmh
        self.hard_limit_kmh = hard_limit_kmh
        super().__init__(str(self))

    def __str__(self) -> str:
        if not isfinite(self.actual_speed_kmh):
            return "research live-smoke actual speed must be finite"
        return (
            "research live-smoke actual speed "
            f"{self.actual_speed_kmh} km/h exceeds {self.hard_limit_kmh} km/h"
        )


def observe_live_smoke_speed_kmh(vehicle: _SpeedSource) -> float:
    velocity = vehicle.get_velocity()
    speed_kmh = calculate_speed_kmh(velocity.x, velocity.y, velocity.z)
    if not isfinite(speed_kmh) or (
        speed_kmh > RESEARCH_LIVE_SMOKE_MAX_ACTUAL_SPEED_KMH
    ):
        raise ResearchNoAActualSpeedSafetyError(speed_kmh)
    return speed_kmh


class _RecordingStepBackend:
    def __init__(
        self,
        backend: _StepBackend,
        vehicle: _VehicleDiagnosticsSource,
        diagnostics: ResearchNoADiagnostics,
    ) -> None:
        self._backend = backend
        self._vehicle = vehicle
        self._diagnostics = diagnostics
        self.commands: list[NoAControlCommand] = []

    def step(self) -> NoAControlCommand:
        speed_kmh = observe_live_smoke_speed_kmh(self._vehicle)
        self._diagnostics.record_speed(speed_kmh)
        command = self._backend.step()
        self.commands.append(command)
        self._diagnostics.record_command(command)
        self._diagnostics.record_gear(int(self._vehicle.get_control().gear))
        return command


class ResearchAutomationScheduler:
    def __init__(
        self,
        runtime: AutomationRuntimeStateSource,
        backend: _StepBackend,
        vehicle: _VehicleDiagnosticsSource,
    ) -> None:
        self.diagnostics = ResearchNoADiagnostics()
        self._recording_backend = _RecordingStepBackend(
            backend,
            vehicle,
            self.diagnostics,
        )
        self.update_count = 0
        self.runtime = runtime
        self._scheduler = AutomationControlScheduler(runtime, self._recording_backend)

    @property
    def commands(self) -> tuple[NoAControlCommand, ...]:
        return tuple(self._recording_backend.commands)

    def update(self) -> bool:
        self.update_count += 1
        started_at = self.diagnostics.timestamp()
        try:
            return self._scheduler.update()
        finally:
            self.diagnostics.record_scheduler_duration(
                self.diagnostics.timestamp() - started_at
            )


class ResearchDryRunScheduler:
    def __init__(self, runtime: AutomationRuntimeStateSource) -> None:
        self.runtime = runtime

    def update(self) -> bool:
        return False
