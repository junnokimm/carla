from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.lane_change import (
    AdjacentLaneObservation,
    LaneChangeDirection,
    LaneChangeEligibility,
)
from src.vehicle.carla_lane_topology import (
    CarlaLanePairTopology,
    capture_lane_pair_topology,
)


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

    @property
    def road_id(self) -> int: ...

    @property
    def section_id(self) -> int: ...

    @property
    def is_junction(self) -> bool: ...

    @property
    def lane_change(self) -> carla.LaneChange: ...


class CarlaAdjacentLaneVehicle(Protocol):
    def get_transform(self) -> CarlaAdjacentLaneTransform: ...


class CarlaAdjacentLaneMap(Protocol):
    def get_waypoint(
        self,
        location: CarlaAdjacentLaneLocation,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ) -> CarlaAdjacentLaneWaypoint | None: ...

    def get_topology(
        self,
    ) -> list[tuple[CarlaAdjacentLaneWaypoint, CarlaAdjacentLaneWaypoint]]: ...


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

    def evaluate(
        self,
        vehicle: CarlaAdjacentLaneVehicle | None,
        direction: LaneChangeDirection,
    ) -> LaneChangeEligibility:
        if vehicle is None:
            raise CarlaAdjacentLaneUnavailableError("vehicle")
        waypoint = self._map.get_waypoint(
            vehicle.get_transform().location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if waypoint is None:
            raise CarlaAdjacentLaneUnavailableError("driving lane waypoint")
        target = (
            waypoint.get_left_lane()
            if direction is LaneChangeDirection.LEFT
            else waypoint.get_right_lane()
        )
        permission = (
            carla.LaneChange.Left
            if direction is LaneChangeDirection.LEFT
            else carla.LaneChange.Right
        )
        marking_permitted = bool(waypoint.lane_change & permission)
        if target is None:
            return LaneChangeEligibility(
                direction,
                waypoint.road_id,
                waypoint.section_id,
                waypoint.lane_id,
                None,
                None,
                None,
                None,
                None,
                marking_permitted,
                False,
                "ADJACENT_LANE_MISSING",
            )
        driving = bool(target.lane_type & carla.LaneType.Driving)
        same_direction = waypoint.lane_id * target.lane_id > 0
        same_segment = (
            waypoint.road_id == target.road_id
            and waypoint.section_id == target.section_id
        )
        outside_junction = not waypoint.is_junction and not target.is_junction
        if not outside_junction:
            reason = "LANE_CHANGE_JUNCTION_UNSUPPORTED"
        elif not same_segment:
            reason = "ADJACENT_LANE_DIFFERENT_ROAD_SECTION"
        elif not driving:
            reason = "ADJACENT_LANE_NOT_DRIVING"
        elif not same_direction:
            reason = "ADJACENT_LANE_OPPOSITE_DIRECTION"
        elif not marking_permitted:
            reason = "LANE_CHANGE_MARKING_PROHIBITS_DIRECTION"
        else:
            reason = "STATIC_ELIGIBLE"
        return LaneChangeEligibility(
            direction,
            waypoint.road_id,
            waypoint.section_id,
            waypoint.lane_id,
            target.road_id,
            target.section_id,
            target.lane_id,
            driving,
            same_direction,
            marking_permitted,
            reason == "STATIC_ELIGIBLE",
            reason,
        )

    def capture_topology(
        self,
        vehicle: CarlaAdjacentLaneVehicle | None,
        direction: LaneChangeDirection,
    ) -> CarlaLanePairTopology | None:
        if vehicle is None:
            raise CarlaAdjacentLaneUnavailableError("vehicle")
        return capture_lane_pair_topology(
            self._map,
            vehicle.get_transform(),
            direction,
        )


def _driving_lane_id(waypoint: CarlaAdjacentLaneWaypoint | None) -> int | None:
    if waypoint is None:
        return None
    if waypoint.lane_type & carla.LaneType.Driving != carla.LaneType.Driving:
        return None
    return waypoint.lane_id
