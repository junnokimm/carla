from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import carla

from src.config import CARLA_HOST, CARLA_PORT, CARLA_TIMEOUT
from src.scenario.highway_geometry import road_lengths
from src.scenario.highway_xodr import OPPOSITE_LANE_IDS, PRIMARY_LANE_IDS


class OpenDriveHighwayError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RoadNetworkValidation:
    road_ids: tuple[int, ...]
    lane_ids: tuple[int, ...]
    checked_connections: int


@dataclass(frozen=True, slots=True)
class WaypointObservation:
    road_id: int
    lane_id: int
    lane_width: float
    lane_type: str
    heading: float
    left_lane_id: int | None
    right_lane_id: int | None


MAP_PATH = Path(__file__).parents[2] / "maps" / "highway_loop_3lane.xodr"
ROAD_IDS = (1, 2, 3, 4)


def _parameters() -> carla.OpendriveGenerationParameters:
    return carla.OpendriveGenerationParameters(
        vertex_distance=2.0,
        max_road_length=50.0,
        wall_height=0.0,
        additional_width=0.6,
        smooth_junctions=True,
        enable_mesh_visibility=True,
    )


def load_highway(client: carla.Client, map_path: Path = MAP_PATH) -> carla.World:
    try:
        xodr_xml = map_path.read_text(encoding="utf-8")
    except OSError as error:
        raise OpenDriveHighwayError(
            f"Unable to read OpenDRIVE map: {map_path}"
        ) from error
    try:
        return client.generate_opendrive_world(
            xodr_xml, _parameters(), reset_settings=False
        )
    except RuntimeError as error:
        raise OpenDriveHighwayError(
            "CARLA could not generate the OpenDRIVE world."
        ) from error


def validate_road_network(world_map: carla.Map) -> RoadNetworkValidation:
    lane_ids = (*PRIMARY_LANE_IDS[::-1], *OPPOSITE_LANE_IDS)
    connections = 0
    for road_id, road_length in zip(ROAD_IDS, road_lengths(), strict=True):
        successor_id = road_id % len(ROAD_IDS) + 1
        predecessor_id = (road_id - 2) % len(ROAD_IDS) + 1
        for lane_id in PRIMARY_LANE_IDS:
            waypoint = world_map.get_waypoint_xodr(road_id, lane_id, road_length - 0.1)
            if waypoint is None:
                raise OpenDriveHighwayError(
                    f"Missing driving waypoint for road {road_id}, lane {lane_id}."
                )
            successors = waypoint.next(2.0)
            if not any(
                successor.road_id == successor_id and successor.lane_id == lane_id
                for successor in successors
            ):
                raise OpenDriveHighwayError(
                    f"Road {road_id}, lane {lane_id} does not continue to road {successor_id}."
                )
            connections += 1
        for lane_id in OPPOSITE_LANE_IDS:
            waypoint = world_map.get_waypoint_xodr(road_id, lane_id, 0.1)
            if waypoint is None:
                raise OpenDriveHighwayError(
                    f"Missing driving waypoint for road {road_id}, lane {lane_id}."
                )
            predecessors = waypoint.next(2.0)
            if not any(
                predecessor.road_id == predecessor_id and predecessor.lane_id == lane_id
                for predecessor in predecessors
            ):
                raise OpenDriveHighwayError(
                    f"Road {road_id}, lane {lane_id} does not continue to road {predecessor_id}."
                )
            connections += 1
    return RoadNetworkValidation(ROAD_IDS, lane_ids, connections)


def inspect_waypoints(world_map: carla.Map) -> tuple[WaypointObservation, ...]:
    observations: list[WaypointObservation] = []
    for road_id, road_length in zip(ROAD_IDS, road_lengths(), strict=True):
        for lane_id in (*PRIMARY_LANE_IDS, *OPPOSITE_LANE_IDS):
            waypoint = world_map.get_waypoint_xodr(road_id, lane_id, road_length / 2.0)
            if waypoint is None:
                raise OpenDriveHighwayError(
                    f"Missing driving waypoint for road {road_id}, lane {lane_id}."
                )
            left_lane = waypoint.get_left_lane()
            right_lane = waypoint.get_right_lane()
            observations.append(
                WaypointObservation(
                    road_id=road_id,
                    lane_id=lane_id,
                    lane_width=waypoint.lane_width,
                    lane_type=str(waypoint.lane_type),
                    heading=waypoint.transform.rotation.yaw,
                    left_lane_id=left_lane.lane_id if left_lane is not None else None,
                    right_lane_id=right_lane.lane_id
                    if right_lane is not None
                    else None,
                )
            )
    return tuple(observations)


def validate_opposite_directions(world_map: carla.Map) -> None:
    for road_id, road_length in zip(ROAD_IDS, road_lengths(), strict=True):
        primary = world_map.get_waypoint_xodr(
            road_id, PRIMARY_LANE_IDS[0], road_length / 2.0
        )
        opposite = world_map.get_waypoint_xodr(
            road_id, OPPOSITE_LANE_IDS[0], road_length / 2.0
        )
        if primary is None or opposite is None:
            raise OpenDriveHighwayError(
                f"Missing directional waypoints on road {road_id}."
            )
        primary_heading = math.radians(primary.transform.rotation.yaw)
        opposite_heading = math.radians(opposite.transform.rotation.yaw)
        heading_dot = math.cos(primary_heading - opposite_heading)
        if heading_dot > -0.99:
            raise OpenDriveHighwayError(
                f"Road {road_id} carriageways are not oppositely oriented."
            )


def _run_vehicle_smoke_test(
    world: carla.World, road_id: int, lane_id: int, s: float, seconds: float
) -> None:
    waypoint = world.get_map().get_waypoint_xodr(road_id, lane_id, s)
    if waypoint is None:
        raise OpenDriveHighwayError("Cannot find the smoke-test spawn waypoint.")
    blueprint = world.get_blueprint_library().filter("vehicle.*")[0]
    transform = waypoint.transform
    transform.location.z += 0.5
    vehicle = world.try_spawn_actor(blueprint, transform)
    if vehicle is None:
        raise OpenDriveHighwayError("Unable to spawn the autopilot smoke-test vehicle.")
    try:
        vehicle.set_autopilot(True)
        origin = vehicle.get_location()
        time.sleep(seconds)
        if not vehicle.is_alive:
            raise OpenDriveHighwayError("Autopilot smoke-test vehicle was destroyed.")
        location = vehicle.get_location()
        travelled = location.distance(origin)
        current = world.get_map().get_waypoint(
            location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
        if travelled <= 1.0:
            raise OpenDriveHighwayError(
                f"Autopilot did not move far enough from lane {lane_id}: {travelled:.1f} m."
            )
        if current is None or current.lane_id * lane_id <= 0:
            raise OpenDriveHighwayError(
                f"Autopilot left the expected carriageway from lane {lane_id}."
            )
        print(
            f"Autopilot lane {lane_id} travelled {travelled:.1f} m "
            f"from road {road_id} to road {current.road_id}."
        )
    finally:
        vehicle.destroy()


def _run_autopilot_smoke_test(world: carla.World, seconds: float) -> None:
    first_road_length = road_lengths()[0]
    _run_vehicle_smoke_test(world, 1, -3, 2990.0, seconds)
    _run_vehicle_smoke_test(world, 2, -3, 1530.0, seconds)
    _run_vehicle_smoke_test(world, 1, -3, first_road_length - 1.0, seconds)
    _run_vehicle_smoke_test(world, 1, 3, 1.0, seconds)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and validate the generated three-lane OpenDRIVE highway."
    )
    parser.add_argument("--host", default=CARLA_HOST)
    parser.add_argument("--port", type=int, default=CARLA_PORT)
    parser.add_argument("--timeout", type=float, default=CARLA_TIMEOUT)
    parser.add_argument("--autopilot-seconds", type=float, default=0.0)
    arguments = parser.parse_args(argv)
    if arguments.timeout <= 0 or arguments.autopilot_seconds < 0:
        parser.error(
            "timeout must be positive and autopilot-seconds cannot be negative"
        )
    return arguments


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    client = carla.Client(arguments.host, arguments.port)
    client.set_timeout(arguments.timeout)
    world = load_highway(client)
    report = validate_road_network(world.get_map())
    validate_opposite_directions(world.get_map())
    print(
        "Loaded highway loop: "
        f"roads={report.road_ids}, lanes={report.lane_ids}, "
        f"connections={report.checked_connections}."
    )
    for observation in inspect_waypoints(world.get_map()):
        print(observation)
    if arguments.autopilot_seconds:
        _run_autopilot_smoke_test(world, arguments.autopilot_seconds)


if __name__ == "__main__":
    main()
