from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import dataclass

import carla

from src.config import CARLA_HOST, CARLA_PORT, CARLA_TIMEOUT
from src.scenario.driver_view import DriverView


class HighwayScenarioError(RuntimeError):
    """Raised when the minimal highway scenario cannot be created."""


@dataclass(frozen=True, slots=True)
class CarlaConnection:
    host: str = CARLA_HOST
    port: int = CARLA_PORT
    timeout: float = CARLA_TIMEOUT


@dataclass(frozen=True, slots=True)
class HighwayScenarioRunConfig:
    duration: float = 20.0
    driver_view: bool = False


class HighwayScenario:
    """Spawn, autopilot, and clean up one CARLA hero vehicle."""

    def __init__(
        self,
        client: carla.Client | None = None,
        connection: CarlaConnection | None = None,
    ) -> None:
        settings = connection or CarlaConnection()
        self._client = client or carla.Client(settings.host, settings.port)
        if client is None:
            self._client.set_timeout(settings.timeout)
        self._actors: list[carla.Actor] = []

    @property
    def actors(self) -> tuple[carla.Actor, ...]:
        return tuple(self._actors)

    @property
    def world(self) -> carla.World:
        """Return the CARLA world used by this scenario."""
        return self._client.get_world()

    def current_map_name(self) -> str:
        return self.world.get_map().name

    def setup(self) -> carla.Actor:
        if self._actors:
            raise HighwayScenarioError(
                "Highway scenario already has a spawned hero vehicle."
            )

        world = self.world
        blueprints = world.get_blueprint_library().filter("vehicle.*")
        if len(blueprints) == 0:
            raise HighwayScenarioError(
                "No vehicle blueprint is available in the current CARLA world."
            )

        spawn_points = world.get_map().get_spawn_points()
        if len(spawn_points) == 0:
            raise HighwayScenarioError(
                "No vehicle spawn point is available on the current CARLA map."
            )

        blueprint = blueprints[0]
        if blueprint.has_attribute("role_name"):
            blueprint.set_attribute("role_name", "hero")
        try:
            hero = world.spawn_actor(blueprint, spawn_points[0])
        except RuntimeError as error:
            raise HighwayScenarioError("Unable to spawn hero vehicle.") from error

        self._actors.append(hero)
        hero.set_autopilot(True)
        return hero

    def cleanup(self) -> int:
        actors = tuple(self._actors)
        self._actors.clear()
        for actor in actors:
            actor.destroy()
        return len(actors)


def parse_arguments(argv: Sequence[str] | None = None) -> HighwayScenarioRunConfig:
    parser = argparse.ArgumentParser(
        description="Run a minimal CARLA highway scenario."
    )
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--driver-view", action="store_true")
    arguments = parser.parse_args(argv)
    duration = arguments.duration
    if duration <= 0:
        raise HighwayScenarioError("duration must be positive")
    return HighwayScenarioRunConfig(
        duration=duration, driver_view=arguments.driver_view
    )


def main(argv: Sequence[str] | None = None) -> None:
    config = parse_arguments(argv)
    scenario = HighwayScenario()
    viewer: DriverView | None = None
    try:
        print(f"Current map: {scenario.current_map_name()}")
        hero = scenario.setup()
        print(f"Spawned hero vehicle {hero.id}; autopilot enabled.")
        if config.driver_view:
            viewer = DriverView(scenario.world, hero)
            viewer.attach()
            viewer.run(config.duration)
        else:
            time.sleep(config.duration)
        print("Scenario completed.")
    except KeyboardInterrupt:
        print("Scenario interrupted.")
    finally:
        if viewer is not None:
            viewer.close()
        if scenario.cleanup() > 0:
            print("Destroyed spawned hero vehicle.")


if __name__ == "__main__":
    main()
