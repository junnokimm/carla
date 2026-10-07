from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import carla

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.automation_scheduler import AutomationControlScheduler
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_control import NoAControlCommand
from src.vehicle.carla_adjacent_lanes import (
    CarlaAdjacentLaneAdapter,
    CarlaAdjacentLaneLocation,
    CarlaAdjacentLaneTransform,
    CarlaAdjacentLaneWaypoint,
)
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryAdapter,
    CarlaLocation,
    CarlaRotation,
    CarlaTransform,
    CarlaWaypoint,
)
from src.vehicle.carla_noa_control import CarlaVelocity
from src.vehicle.carla_noa_simulation_control import (
    DetailedLaneGeometrySource,
    SimulationTimeCarlaNoAControlBackend,
)
from src.vehicle.driving_mode import AutopilotVehicle


class _NoAControlBackend(Protocol):
    def enter_manual_control(self) -> None: ...

    def enter_noa_control(self) -> None: ...

    def step(self) -> NoAControlCommand: ...


class CarlaNoARuntimeLocation(
    CarlaLocation,
    CarlaAdjacentLaneLocation,
    Protocol,
):
    pass


class CarlaNoARuntimeTransform(
    CarlaTransform,
    CarlaAdjacentLaneTransform,
    Protocol,
):
    @property
    def location(self) -> CarlaNoARuntimeLocation: ...

    @property
    def rotation(self) -> CarlaRotation: ...


class CarlaNoARuntimeWaypoint(
    CarlaWaypoint,
    CarlaAdjacentLaneWaypoint,
    Protocol,
):
    @property
    def transform(self) -> CarlaNoARuntimeTransform: ...

    def get_left_lane(self) -> CarlaNoARuntimeWaypoint | None: ...

    def get_right_lane(self) -> CarlaNoARuntimeWaypoint | None: ...


class CarlaNoARuntimeVehicle(AutopilotVehicle, Protocol):
    def get_transform(self) -> CarlaNoARuntimeTransform: ...

    def get_velocity(self) -> CarlaVelocity: ...

    def apply_control(self, control: carla.VehicleControl) -> None: ...


class CarlaNoARuntimeMap(Protocol):
    def get_waypoint(
        self,
        location: CarlaLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> CarlaNoARuntimeWaypoint | None: ...


@dataclass(frozen=True, slots=True)
class NoAControlConfig:
    longitudinal: LongitudinalControlConfig
    lateral: LateralControlConfig


@dataclass(frozen=True, slots=True)
class SingleControlOwnershipBackend:
    vehicle: AutopilotVehicle
    custom_backend: _NoAControlBackend

    def enter_noa_control(self) -> None:
        self.vehicle.set_autopilot(False)
        self.custom_backend.enter_noa_control()

    def enter_manual_control(self) -> None:
        self.vehicle.set_autopilot(False)
        self.custom_backend.enter_manual_control()

    def step(self) -> NoAControlCommand:
        return self.custom_backend.step()


@dataclass(frozen=True, slots=True)
class NoARuntimeBundle:
    automation_runtime: AutomationRuntimeController
    scheduler: AutomationControlScheduler
    control_backend: SimulationTimeCarlaNoAControlBackend
    ownership_backend: SingleControlOwnershipBackend
    lane_geometry: CarlaLaneGeometryAdapter
    adjacent_lanes: CarlaAdjacentLaneAdapter
    control_lane_geometry: DetailedLaneGeometrySource


def build_noa_runtime(
    vehicle: CarlaNoARuntimeVehicle,
    carla_map: CarlaNoARuntimeMap,
    control_config: NoAControlConfig,
    *,
    control_clock: Callable[[], float] = monotonic,
    control_lane_geometry: DetailedLaneGeometrySource | None = None,
) -> NoARuntimeBundle:
    lane_geometry = CarlaLaneGeometryAdapter(carla_map)
    adjacent_lanes = CarlaAdjacentLaneAdapter(carla_map)
    backend_geometry = control_lane_geometry or lane_geometry
    control_backend = SimulationTimeCarlaNoAControlBackend(
        vehicle,
        backend_geometry,
        control_config.longitudinal,
        control_config.lateral,
        control_clock=control_clock,
    )
    ownership_backend = SingleControlOwnershipBackend(vehicle, control_backend)
    ownership_backend.enter_manual_control()
    automation_runtime = AutomationRuntimeController(
        AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.MANUAL,
        ),
        ownership_backend,
    )
    scheduler = AutomationControlScheduler(automation_runtime, ownership_backend)
    return NoARuntimeBundle(
        automation_runtime=automation_runtime,
        scheduler=scheduler,
        control_backend=control_backend,
        ownership_backend=ownership_backend,
        lane_geometry=lane_geometry,
        adjacent_lanes=adjacent_lanes,
        control_lane_geometry=backend_geometry,
    )
