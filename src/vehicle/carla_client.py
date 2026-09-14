from __future__ import annotations

import math

import carla

from src.config import CARLA_HOST, CARLA_PORT, CARLA_TIMEOUT
from src.vehicle import VehicleState


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

        velocity = hero.get_velocity()
        control = hero.get_control()
        waypoint = world.get_map().get_waypoint(
            hero.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving
        )
        lights = hero.get_light_state()
        left = bool(lights & carla.VehicleLightState.LeftBlinker)
        right = bool(lights & carla.VehicleLightState.RightBlinker)

        return VehicleState(
            timestamp=world.get_snapshot().timestamp.elapsed_seconds,
            speed_kmh=math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2) * 3.6,
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
        )
