from __future__ import annotations

from dataclasses import dataclass
from math import radians
from typing import Protocol

import carla

from src.experiment.lane_geometry import (
    LaneGeometryObservation,
    PlanarPose,
    compute_lane_relative_geometry,
)


class CarlaLocation(Protocol):
    @property
    def x(self) -> float: ...

    @property
    def y(self) -> float: ...


class CarlaRotation(Protocol):
    @property
    def yaw(self) -> float: ...


class CarlaTransform(Protocol):
    @property
    def location(self) -> CarlaLocation: ...

    @property
    def rotation(self) -> CarlaRotation: ...


class CarlaWaypoint(Protocol):
    @property
    def transform(self) -> CarlaTransform: ...


class CarlaVehicle(Protocol):
    def get_transform(self) -> CarlaTransform: ...


class CarlaMap(Protocol):
    def get_waypoint(
        self,
        location: CarlaLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> CarlaWaypoint | None: ...


@dataclass(frozen=True, slots=True)
class CarlaLaneGeometryUnavailableError(RuntimeError):
    resource: str

    def __str__(self) -> str:
        return f"CARLA lane geometry requires an available {self.resource}"


class CarlaLaneGeometryAdapter:
    def __init__(self, carla_map: CarlaMap | None) -> None:
        if carla_map is None:
            raise CarlaLaneGeometryUnavailableError("map")
        self._map = carla_map

    def observe(self, vehicle: CarlaVehicle | None) -> LaneGeometryObservation:
        if vehicle is None:
            raise CarlaLaneGeometryUnavailableError("vehicle")
        vehicle_transform = vehicle.get_transform()
        waypoint = self._map.get_waypoint(
            vehicle_transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if waypoint is None:
            raise CarlaLaneGeometryUnavailableError("driving lane waypoint")

        lane_transform = waypoint.transform
        return compute_lane_relative_geometry(
            PlanarPose(
                x=vehicle_transform.location.x,
                y=vehicle_transform.location.y,
                yaw_rad=radians(vehicle_transform.rotation.yaw),
            ),
            PlanarPose(
                x=lane_transform.location.x,
                y=lane_transform.location.y,
                yaw_rad=radians(lane_transform.rotation.yaw),
            ),
        )
