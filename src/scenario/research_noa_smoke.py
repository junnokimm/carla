from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Final, Protocol

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_scheduler import (
    AutomationControlScheduler,
    AutomationRuntimeStateSource,
)
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_transmission import ResearchTransmissionPrimeTelemetry
from src.scenario.research_noa_types import ResearchNoAMap, ResearchNoAVehicle
from src.vehicle.carla_noa_control import CarlaVelocity
from src.vehicle.speed import calculate_speed_kmh

RESEARCH_LIVE_SMOKE_MAX_ACTUAL_SPEED_KMH: Final = 20.0


class _StepBackend(Protocol):
    def step(self) -> NoAControlCommand: ...


class _SpeedSource(Protocol):
    def get_velocity(self) -> CarlaVelocity: ...


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
    def __init__(self, backend: _StepBackend, vehicle: _SpeedSource) -> None:
        self._backend = backend
        self._vehicle = vehicle
        self.commands: list[NoAControlCommand] = []
        self.speed_samples_kmh: list[float] = []

    def step(self) -> NoAControlCommand:
        speed_kmh = observe_live_smoke_speed_kmh(self._vehicle)
        self.speed_samples_kmh.append(speed_kmh)
        command = self._backend.step()
        self.commands.append(command)
        return command


class ResearchAutomationScheduler:
    def __init__(
        self,
        runtime: AutomationRuntimeStateSource,
        backend: _StepBackend,
        vehicle: _SpeedSource,
    ) -> None:
        self._recording_backend = _RecordingStepBackend(backend, vehicle)
        self.update_count = 0
        self.runtime = runtime
        self._scheduler = AutomationControlScheduler(runtime, self._recording_backend)

    @property
    def commands(self) -> tuple[NoAControlCommand, ...]:
        return tuple(self._recording_backend.commands)

    @property
    def speed_samples_kmh(self) -> tuple[float, ...]:
        return tuple(self._recording_backend.speed_samples_kmh)

    def update(self) -> bool:
        self.update_count += 1
        return self._scheduler.update()


class ResearchDryRunScheduler:
    def __init__(self, runtime: AutomationRuntimeStateSource) -> None:
        self.runtime = runtime

    def update(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ResearchSmokeReport:
    map_name: str
    spawn_index: int
    spawn_x: float
    spawn_y: float
    spawn_z: float
    spawn_yaw: float
    vehicle_id: int
    vehicle_type: str
    initial_lane_id: int
    final_lane_id: int | None
    target_speed_kmh: float
    initial_speed_kmh: float
    final_speed_kmh: float
    maximum_observed_speed_kmh: float
    initial_gear: int
    transmission_prime_required: bool
    transmission_prime_applied: bool
    post_prime_gear: int
    duration: float
    elapsed_seconds: float
    shutdown_brake: float
    scheduler_updates: int
    commands: tuple[NoAControlCommand, ...]
    user_exited: bool
    final_control_mode: DrivingControlMode

    @property
    def control_frames(self) -> int:
        return len(self.commands)

    @property
    def lane_changed(self) -> bool | None:
        if self.final_lane_id is None:
            return None
        return self.initial_lane_id != self.final_lane_id

    def format(self) -> str:
        throttle = max((command.throttle for command in self.commands), default=0.0)
        brake = max((command.brake for command in self.commands), default=0.0)
        steering = max(
            (abs(command.steering) for command in self.commands),
            default=0.0,
        )
        fields = (
            ("mode", ResearchNoARunMode.LIVE_SMOKE.value),
            ("preflight", "passed"),
            ("map_name", self.map_name),
            ("spawn_index", self.spawn_index),
            ("spawn_x", self.spawn_x),
            ("spawn_y", self.spawn_y),
            ("spawn_z", self.spawn_z),
            ("spawn_yaw", self.spawn_yaw),
            ("vehicle_id", self.vehicle_id),
            ("vehicle_type", self.vehicle_type),
            ("initial_lane_id", self.initial_lane_id),
            ("final_lane_id", self.final_lane_id),
            ("target_speed_kmh", self.target_speed_kmh),
            ("initial_speed_kmh", self.initial_speed_kmh),
            ("final_speed_kmh", self.final_speed_kmh),
            ("maximum_observed_speed_kmh", self.maximum_observed_speed_kmh),
            ("initial_gear", self.initial_gear),
            ("transmission_prime_required", self.transmission_prime_required),
            ("transmission_prime_applied", self.transmission_prime_applied),
            ("post_prime_gear", self.post_prime_gear),
            ("requested_duration_seconds", self.duration),
            ("elapsed_seconds", round(self.elapsed_seconds, 3)),
            ("scheduler_updates", self.scheduler_updates),
            ("control_frames", self.control_frames),
            ("shutdown_brake", self.shutdown_brake),
            ("max_observed_throttle", throttle),
            ("max_observed_brake", brake),
            ("max_observed_abs_steering", steering),
            ("lane_changed", self.lane_changed),
            ("user_exited", self.user_exited),
            ("final_control_mode", self.final_control_mode.value.lower()),
        )
        return "\n".join(f"{name}={value}" for name, value in fields)


def build_research_smoke_report(
    *,
    config: ResearchNoARunConfig,
    world_map: ResearchNoAMap,
    hero: ResearchNoAVehicle,
    spawn_transform: carla.Transform,
    spawn_index: int,
    initial_lane_id: int,
    final_lane_id: int | None,
    initial_speed_kmh: float,
    final_speed_kmh: float,
    transmission_prime: ResearchTransmissionPrimeTelemetry,
    elapsed_seconds: float,
    user_exited: bool,
    scheduler: ResearchAutomationScheduler,
) -> ResearchSmokeReport:
    location = spawn_transform.location
    rotation = spawn_transform.rotation
    return ResearchSmokeReport(
        map_name=str(world_map.name),
        spawn_index=spawn_index,
        spawn_x=float(location.x),
        spawn_y=float(location.y),
        spawn_z=float(location.z),
        spawn_yaw=float(rotation.yaw),
        vehicle_id=int(hero.id),
        vehicle_type=str(hero.type_id),
        initial_lane_id=initial_lane_id,
        final_lane_id=final_lane_id,
        target_speed_kmh=config.control_config.longitudinal.target_speed_kmh,
        initial_speed_kmh=initial_speed_kmh,
        final_speed_kmh=final_speed_kmh,
        maximum_observed_speed_kmh=max(
            initial_speed_kmh,
            *scheduler.speed_samples_kmh,
            final_speed_kmh,
        ),
        initial_gear=transmission_prime.initial_gear,
        transmission_prime_required=transmission_prime.required,
        transmission_prime_applied=transmission_prime.applied,
        post_prime_gear=transmission_prime.post_prime_gear,
        duration=config.duration,
        elapsed_seconds=elapsed_seconds,
        shutdown_brake=config.control_config.longitudinal.max_brake,
        scheduler_updates=scheduler.update_count,
        commands=scheduler.commands,
        user_exited=user_exited,
        final_control_mode=scheduler.runtime.state.control_mode,
    )
