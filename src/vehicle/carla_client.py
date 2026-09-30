from __future__ import annotations

import math
import time

import carla

from src.config import CARLA_HOST, CARLA_PORT, CARLA_TIMEOUT
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.vehicle import VehicleObservation, VehicleState


class HeroVehicleNotFoundError(RuntimeError):
    """Raised when the current CARLA world has no vehicle with role_name 'hero'."""


class CarlaVehicleClient:
    """Read the current CARLA hero vehicle telemetry without controlling it."""

    def __init__(
        self,
        host: str = CARLA_HOST,
        port: int = CARLA_PORT,
        timeout: float = CARLA_TIMEOUT,
    ) -> None:
        self._client = carla.Client(host, port)
        self._client.set_timeout(timeout)

    def get_state(self) -> VehicleState:
        return self.get_observation().state

    def get_observation(self) -> VehicleObservation:
        world = self._client.get_world()
        hero = next(
            (
                vehicle
                for vehicle in world.get_actors().filter("vehicle.*")
                if vehicle.attributes.get("role_name") == "hero"
            ),
            None,
        )
        if hero is None:
            raise HeroVehicleNotFoundError(
                "No ego vehicle with role_name 'hero' exists in the current CARLA world."
            )

        capture_started_monotonic_ns = time.monotonic_ns()
        snapshot = world.get_snapshot()
        capture_completed_monotonic_ns = time.monotonic_ns()
        host_utc_ns = time.time_ns()

        velocity = hero.get_velocity()
        control = hero.get_control()
        waypoint = world.get_map().get_waypoint(
            hero.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving
        )
        lights = hero.get_light_state()
        left = bool(lights & carla.VehicleLightState.LeftBlinker)
        right = bool(lights & carla.VehicleLightState.RightBlinker)

        simulation_seconds = snapshot.timestamp.elapsed_seconds
        return VehicleObservation(
            timestamp=TimestampEnvelope(
                host=HostClockTimestamp(
                    monotonic_ns=capture_completed_monotonic_ns,
                    utc_ns=host_utc_ns,
                ),
                carla_snapshot=CarlaSnapshotTimestamp(
                    simulation_seconds=simulation_seconds,
                    frame=snapshot.frame,
                    host_capture_started_monotonic_ns=capture_started_monotonic_ns,
                    host_capture_completed_monotonic_ns=capture_completed_monotonic_ns,
                ),
            ),
            state=VehicleState(
                timestamp=simulation_seconds,
                speed_kmh=(
                    math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2) * 3.6
                ),
                steering=control.steer,
                throttle=control.throttle,
                brake=control.brake,
                lane_id=waypoint.lane_id if waypoint is not None else None,
                indicator=(
                    "hazard"
                    if left and right
                    else "left"
                    if left
                    else "right"
                    if right
                    else "off"
                ),
            ),
        )
