from __future__ import annotations

from typing import Protocol

import carla

from src.experiment.noa_runtime import (
    CarlaNoARuntimeMap,
    CarlaNoARuntimeVehicle,
)
from src.scenario.driver_view import DriverViewConfig, RuntimeIterationScheduler


class ResearchNoAViewer(Protocol):
    def attach(self) -> None: ...

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
    ) -> bool: ...

    def close(self) -> None: ...


class ResearchNoABlueprint(Protocol):
    def has_attribute(self, name: str) -> bool: ...

    def set_attribute(self, name: str, value: str) -> None: ...


class ResearchNoABlueprintLibrary(Protocol):
    def find(self, identifier: str) -> ResearchNoABlueprint: ...


class ResearchNoAMap(CarlaNoARuntimeMap, Protocol):
    @property
    def name(self) -> str: ...

    def get_spawn_points(self) -> list[carla.Transform]: ...


class ResearchNoAVehicle(CarlaNoARuntimeVehicle, Protocol):
    @property
    def id(self) -> int: ...

    @property
    def type_id(self) -> str: ...

    def get_control(self) -> carla.VehicleControl: ...

    def get_physics_control(self) -> ResearchNoAVehiclePhysicsControl: ...

    def destroy(self) -> bool: ...


class ResearchNoAVehiclePhysicsControl(Protocol):
    gear_switch_time: float


class ResearchNoAWorldSnapshot(Protocol):
    @property
    def frame(self) -> int: ...


class ResearchNoAWorld(Protocol):
    def get_map(self) -> ResearchNoAMap: ...

    def get_blueprint_library(self) -> ResearchNoABlueprintLibrary: ...

    def spawn_actor(
        self,
        blueprint: ResearchNoABlueprint,
        transform: carla.Transform,
    ) -> ResearchNoAVehicle: ...

    def wait_for_tick(self, seconds: float) -> ResearchNoAWorldSnapshot: ...


class ResearchNoAClient(Protocol):
    def get_world(self) -> ResearchNoAWorld: ...


class ResearchNoAViewerFactory(Protocol):
    def __call__(
        self,
        world: ResearchNoAWorld,
        hero: ResearchNoAVehicle,
        config: DriverViewConfig,
    ) -> ResearchNoAViewer: ...
