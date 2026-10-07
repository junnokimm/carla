from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import carla

from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig, compute_lateral_control
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_control import NoAControlCommand
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext, CarlaVehicle
from src.vehicle.carla_noa_control import (
    CarlaNoAControlBackend,
    CarlaNoAControlUnavailableError,
    CarlaNoAControlVehicle,
    CarlaVelocity,
    NoAControlInactiveError,
)
from src.vehicle.speed import calculate_speed_kmh


class NoAControlObservation(Protocol):
    frame: int
    simulation_time_seconds: float
    speed_kmh: float


class CarlaActorSnapshot(Protocol):
    def get_velocity(self) -> CarlaVelocity: ...


class CarlaSnapshotTimestamp(Protocol):
    @property
    def elapsed_seconds(self) -> float: ...


class CarlaControlSnapshot(Protocol):
    @property
    def frame(self) -> int: ...

    @property
    def timestamp(self) -> CarlaSnapshotTimestamp: ...

    def find(self, actor_id: int) -> CarlaActorSnapshot | None: ...


class DetailedLaneGeometrySource(Protocol):
    def observe(
        self,
        vehicle: CarlaVehicle | None,
    ) -> LaneGeometryObservation: ...

    def observe_with_context(
        self,
        vehicle: CarlaVehicle | None,
    ) -> CarlaLaneGeometryContext: ...


@dataclass(frozen=True, slots=True)
class CarlaNoAControlObservation:
    frame: int
    simulation_time_seconds: float
    speed_kmh: float


@dataclass(frozen=True, slots=True)
class CarlaNoAControlStep:
    frame: int
    simulation_time_seconds: float
    delta_seconds: float
    target_speed_kmh: float
    measured_speed_kmh: float
    integral_effort: float
    lane_geometry: CarlaLaneGeometryContext
    command: NoAControlCommand


class CarlaNoAControlTimelineError(RuntimeError):
    def __init__(
        self,
        previous_frame: int,
        previous_time_seconds: float,
        current_frame: int,
        current_time_seconds: float,
    ) -> None:
        self.previous_frame = previous_frame
        self.previous_time_seconds = previous_time_seconds
        self.current_frame = current_frame
        self.current_time_seconds = current_time_seconds
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            "CARLA control snapshot moved backward from "
            f"frame={self.previous_frame}, time={self.previous_time_seconds} to "
            f"frame={self.current_frame}, time={self.current_time_seconds}"
        )


def capture_control_observation(
    snapshot: CarlaControlSnapshot,
    actor_id: int,
) -> CarlaNoAControlObservation:
    actor = snapshot.find(actor_id)
    if actor is None:
        raise CarlaNoAControlUnavailableError("vehicle actor snapshot")
    velocity = actor.get_velocity()
    return CarlaNoAControlObservation(
        frame=int(snapshot.frame),
        simulation_time_seconds=float(snapshot.timestamp.elapsed_seconds),
        speed_kmh=calculate_speed_kmh(velocity.x, velocity.y, velocity.z),
    )


class SimulationTimeCarlaNoAControlBackend(CarlaNoAControlBackend):
    def __init__(
        self,
        vehicle: CarlaNoAControlVehicle | None,
        lane_geometry: DetailedLaneGeometrySource,
        longitudinal_config: LongitudinalControlConfig,
        lateral_config: LateralControlConfig,
        *,
        control_clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(
            vehicle,
            lane_geometry,
            longitudinal_config,
            lateral_config,
            control_clock=control_clock,
        )
        self._detailed_lane_geometry = lane_geometry
        self._last_frame: int | None = None
        self._last_simulation_time_seconds: float | None = None
        self._last_step: CarlaNoAControlStep | None = None

    @property
    def last_step(self) -> CarlaNoAControlStep | None:
        return self._last_step

    def enter_noa_control(self) -> None:
        if not self.active:
            self._reset_simulation_timeline()
        super().enter_noa_control()

    def enter_manual_control(self) -> None:
        super().enter_manual_control()
        self._reset_simulation_timeline()

    def _reset_simulation_timeline(self) -> None:
        self._last_frame = None
        self._last_simulation_time_seconds = None
        self._last_step = None

    def step_from_observation(
        self,
        observation: NoAControlObservation,
    ) -> NoAControlCommand:
        if not self.active:
            raise NoAControlInactiveError
        previous_frame = self._last_frame
        previous_time = self._last_simulation_time_seconds
        if (
            previous_frame is not None
            and previous_time is not None
            and (
                observation.frame < previous_frame
                or observation.simulation_time_seconds < previous_time
            )
        ):
            raise CarlaNoAControlTimelineError(
                previous_frame,
                previous_time,
                observation.frame,
                observation.simulation_time_seconds,
            )
        control_time = observation.simulation_time_seconds
        if observation.frame == previous_frame and previous_time is not None:
            control_time = previous_time

        lane_geometry = self._detailed_lane_geometry.observe_with_context(self._vehicle)
        longitudinal = self._longitudinal_controller.compute(
            observation.speed_kmh,
            control_time_seconds=control_time,
        )
        lateral = compute_lateral_control(
            lane_geometry.geometry.lateral_error_m,
            lane_geometry.geometry.heading_error_rad,
            self._lateral_config,
        )
        command = self._merge_driver_steering(
            NoAControlCommand(
                throttle=longitudinal.throttle,
                brake=longitudinal.brake,
                steering=lateral.steering,
            )
        )
        self._vehicle.apply_control(
            carla.VehicleControl(
                throttle=command.throttle,
                brake=command.brake,
                steer=command.steering,
            )
        )
        self._last_frame = observation.frame
        self._last_simulation_time_seconds = control_time
        self._last_step = CarlaNoAControlStep(
            frame=observation.frame,
            simulation_time_seconds=observation.simulation_time_seconds,
            delta_seconds=self._longitudinal_controller.delta_seconds,
            target_speed_kmh=self._longitudinal_config.target_speed_kmh,
            measured_speed_kmh=observation.speed_kmh,
            integral_effort=self._longitudinal_controller.integral_effort,
            lane_geometry=lane_geometry,
            command=command,
        )
        return command
