from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_control import NoAControlCommand, compute_noa_control
from src.vehicle.carla_lane_geometry import CarlaVehicle
from src.vehicle.speed import calculate_speed_kmh


class CarlaVelocity(Protocol):
    @property
    def x(self) -> float: ...

    @property
    def y(self) -> float: ...

    @property
    def z(self) -> float: ...


class CarlaNoAControlVehicle(CarlaVehicle, Protocol):
    def get_velocity(self) -> CarlaVelocity: ...

    def apply_control(self, control: carla.VehicleControl) -> None: ...


class LaneGeometrySource(Protocol):
    def observe(self, vehicle: CarlaVehicle | None) -> LaneGeometryObservation: ...


@dataclass(frozen=True, slots=True)
class CarlaNoAControlUnavailableError(RuntimeError):
    resource: str

    def __str__(self) -> str:
        return f"CARLA NoA control requires an available {self.resource}"


class NoAControlInactiveError(RuntimeError):
    """Raised when a control step is requested before NoA activation."""

    def __str__(self) -> str:
        return "NoA control backend is inactive"


class CarlaNoAControlBackend:
    """Apply explicit one-tick NoA commands while activation state is mutable."""

    def __init__(
        self,
        vehicle: CarlaNoAControlVehicle | None,
        lane_geometry: LaneGeometrySource,
        longitudinal_config: LongitudinalControlConfig,
        lateral_config: LateralControlConfig,
    ) -> None:
        if vehicle is None:
            raise CarlaNoAControlUnavailableError("vehicle")
        self._vehicle = vehicle
        self._lane_geometry = lane_geometry
        self._longitudinal_config = longitudinal_config
        self._lateral_config = lateral_config
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def enter_noa_control(self) -> None:
        self._active = True

    def enter_manual_control(self) -> None:
        self._active = False

    def step(self) -> NoAControlCommand:
        """Compute, validate, and apply exactly one active NoA control tick."""
        if not self._active:
            raise NoAControlInactiveError

        velocity = self._vehicle.get_velocity()
        current_speed_kmh = calculate_speed_kmh(
            velocity.x,
            velocity.y,
            velocity.z,
        )
        lane_geometry = self._lane_geometry.observe(self._vehicle)
        command = compute_noa_control(
            current_speed_kmh,
            lane_geometry,
            self._longitudinal_config,
            self._lateral_config,
        )
        control = carla.VehicleControl(
            throttle=command.throttle,
            brake=command.brake,
            steer=command.steering,
        )
        self._vehicle.apply_control(control)
        return command
