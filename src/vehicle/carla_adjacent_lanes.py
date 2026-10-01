from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.lane_change import AdjacentLaneObservation


class CarlaAdjacentLaneLocation(Protocol):
    @property
    def x(self) -> float: ...

    @property
    def y(self) -> float: ...

    @property
    def z(self) -> float: ...


class CarlaAdjacentLaneTransform(Protocol):
    @property
    def location(self) -> CarlaAdjacentLaneLocation: ...


class CarlaAdjacentLaneWaypoint(Protocol):
    @property
    def lane_id(self) -> int: ...

    @property
    def lane_type(self) -> carla.LaneType: ...

    def get_left_lane(self) -> CarlaAdjacentLaneWaypoint | None: ...

    def get_right_lane(self) -> CarlaAdjacentLaneWaypoint | None: ...


class CarlaAdjacentLaneVehicle(Protocol):
    def get_transform(self) -> CarlaAdjacentLaneTransform: ...


class CarlaAdjacentLaneMap(Protocol):
    def get_waypoint(
        self,
        location: CarlaAdjacentLaneLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> CarlaAdjacentLaneWaypoint | None: ...


@dataclass(frozen=True, slots=True)
class CarlaAdjacentLaneUnavailableError(RuntimeError):
    """Identify a missing CARLA resource required for lane observation."""

    resource: str

    def __str__(self) -> str:
        return f"CARLA adjacent-lane observation requires an available {self.resource}"


class CarlaAdjacentLaneAdapter:
    """Observe immediate adjacent driving lanes without controlling the vehicle."""

    def __init__(self, carla_map: CarlaAdjacentLaneMap | None) -> None:
        if carla_map is None:
            raise CarlaAdjacentLaneUnavailableError("map")
        self._map = carla_map

    def observe(
        self,
        vehicle: CarlaAdjacentLaneVehicle | None,
    ) -> AdjacentLaneObservation:
        """Return current and immediate adjacent driving-lane identifiers."""
        if vehicle is None:
            raise CarlaAdjacentLaneUnavailableError("vehicle")
        vehicle_transform = vehicle.get_transform()
        waypoint = self._map.get_waypoint(
            vehicle_transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if waypoint is None:
            raise CarlaAdjacentLaneUnavailableError("driving lane waypoint")

        return AdjacentLaneObservation(
            current_lane_id=waypoint.lane_id,
            left_lane_id=_driving_lane_id(waypoint.get_left_lane()),
            right_lane_id=_driving_lane_id(waypoint.get_right_lane()),
        )


def _driving_lane_id(waypoint: CarlaAdjacentLaneWaypoint | None) -> int | None:
    if waypoint is None:
        return None
    if waypoint.lane_type & carla.LaneType.Driving != carla.LaneType.Driving:
        return None
    return waypoint.lane_id
