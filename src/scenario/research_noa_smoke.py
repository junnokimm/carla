from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Final, Protocol, assert_never

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_scheduler import (
    AutomationControlScheduler,
    AutomationRuntimeStateSource,
)
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_types import ResearchNoAWorld
from src.vehicle.carla_noa_control import CarlaVelocity
from src.vehicle.carla_noa_simulation_control import (
    CarlaNoAControlObservation,
    CarlaNoAControlStep,
    capture_control_observation,
)
from src.vehicle.speed import calculate_speed_kmh

RESEARCH_LIVE_SMOKE_MAX_ACTUAL_SPEED_KMH: Final = 20.0


class _StepBackend(Protocol):
    @property
    def last_step(self) -> CarlaNoAControlStep | None: ...

    def step_from_observation(
        self,
        observation: CarlaNoAControlObservation,
    ) -> NoAControlCommand: ...


class _SpeedSource(Protocol):
    def get_velocity(self) -> CarlaVelocity: ...


class _VehicleDiagnosticsSource(_SpeedSource, Protocol):
    @property
    def id(self) -> int: ...

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
    validate_live_smoke_speed_kmh(speed_kmh)
    return speed_kmh


def validate_live_smoke_speed_kmh(speed_kmh: float) -> None:
    if not isfinite(speed_kmh) or (
        speed_kmh > RESEARCH_LIVE_SMOKE_MAX_ACTUAL_SPEED_KMH
    ):
        raise ResearchNoAActualSpeedSafetyError(speed_kmh)


class ResearchNoAMissingControlStepError(RuntimeError):
    pass


class ResearchNoAPiTracePrinter:
    def __init__(self) -> None:
        self._header_written = False

    def __call__(self, step: CarlaNoAControlStep) -> None:
        if not self._header_written:
            print(
                "pi_trace,frame,simulation_time_seconds,actual_pi_dt_seconds,"
                "target_speed_kmh,measured_speed_kmh,integral_effort,"
                "commanded_throttle,commanded_brake"
            )
            self._header_written = True
        print(
            "pi_trace,"
            f"{step.frame},{step.simulation_time_seconds},{step.delta_seconds},"
            f"{step.target_speed_kmh},{step.measured_speed_kmh},"
            f"{step.integral_effort},{step.command.throttle},{step.command.brake}"
        )


class _RecordingStepBackend:
    def __init__(
        self,
        backend: _StepBackend,
        vehicle: _VehicleDiagnosticsSource,
        world: ResearchNoAWorld,
        diagnostics: ResearchNoADiagnostics,
        trace: Callable[[CarlaNoAControlStep], None] | None,
    ) -> None:
        self._backend = backend
        self._vehicle = vehicle
        self._world = world
        self._diagnostics = diagnostics
        self._trace = trace
        self.commands: list[NoAControlCommand] = []

    def step(self) -> NoAControlCommand:
        observation = capture_control_observation(
            self._world.get_snapshot(),
            self._vehicle.id,
        )
        validate_live_smoke_speed_kmh(observation.speed_kmh)
        command = self._backend.step_from_observation(observation)
        step = self._backend.last_step
        if step is None:
            raise ResearchNoAMissingControlStepError
        self._diagnostics.record_speed(step.measured_speed_kmh)
        self.commands.append(command)
        self._diagnostics.record_command(command)
        if self._trace is not None:
            self._trace(step)
        applied = self._vehicle.get_control()
        self._diagnostics.record_applied_control(
            throttle=float(applied.throttle),
            brake=float(applied.brake),
            hand_brake=bool(applied.hand_brake),
            reverse=bool(applied.reverse),
            manual_gear_shift=bool(applied.manual_gear_shift),
        )
        self._diagnostics.record_gear(int(applied.gear))
        return command


class ResearchAutomationScheduler:
    def __init__(
        self,
        runtime: AutomationRuntimeStateSource,
        backend: _StepBackend,
        vehicle: _VehicleDiagnosticsSource,
        world: ResearchNoAWorld,
        trace: Callable[[CarlaNoAControlStep], None] | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
    ) -> None:
        self.diagnostics = diagnostics or ResearchNoADiagnostics()
        self._recording_backend = _RecordingStepBackend(
            backend,
            vehicle,
            world,
            self.diagnostics,
            trace,
        )
        self.update_count = 0
        self.runtime = runtime
        self._vehicle = vehicle
        self._scheduler = AutomationControlScheduler(runtime, self._recording_backend)

    @property
    def commands(self) -> tuple[NoAControlCommand, ...]:
        return tuple(self._recording_backend.commands)

    def update(self) -> bool:
        self.update_count += 1
        started_at = self.diagnostics.timestamp()
        try:
            match self.runtime.state.control_mode:
                case DrivingControlMode.MANUAL:
                    observe_live_smoke_speed_kmh(self._vehicle)
                case DrivingControlMode.NOA_ACTIVE:
                    pass
                case unreachable:
                    assert_never(unreachable)
            return self._scheduler.update()
        finally:
            self.diagnostics.record_scheduler_duration(
                self.diagnostics.timestamp() - started_at
            )


class ResearchDryRunScheduler:
    def __init__(self, runtime: AutomationRuntimeStateSource) -> None:
        self.runtime = runtime
        self.update_count = 0

    def update(self) -> bool:
        self.update_count += 1
        return False
