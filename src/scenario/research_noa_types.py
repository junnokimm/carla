from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import carla

from src.experiment.noa_runtime import (
    CarlaNoARuntimeMap,
    CarlaNoARuntimeVehicle,
)
from src.scenario.driver_view import (
    ResearchPostControlObserver,
    RuntimeDriverInputObserver,
    RuntimeIterationScheduler,
)
from src.vehicle.carla_noa_control import CarlaVelocity

__all__ = ["ResearchPostControlObserver"]

if TYPE_CHECKING:
    from src.scenario.research_camera_metadata import ResearchCameraRunMeasurements
    from src.scenario.research_exit_view import ExitViewBinding
    from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
    from src.scenario.research_noa_view import ResearchDriverViewConfig


class ResearchNoAViewer(Protocol):
    def set_exit_view_binding(self, binding: ExitViewBinding | None) -> None: ...

    def attach(self) -> None: ...

    def capture_camera_diagnostics_environment(
        self,
        world: ResearchNoAWorld,
    ) -> None: ...

    def camera_diagnostics_json(
        self,
        measurements: ResearchCameraRunMeasurements | None = None,
    ) -> str | None: ...

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
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

    @property
    def bounding_box(self) -> carla.BoundingBox: ...

    def get_control(self) -> carla.VehicleControl: ...

    def get_light_state(self) -> carla.VehicleLightState: ...

    def get_physics_control(self) -> ResearchNoAVehiclePhysicsControl: ...

    def destroy(self) -> bool: ...


class ResearchNoAVehiclePhysicsControl(Protocol):
    gear_switch_time: float


class ResearchNoAWorldSnapshot(Protocol):
    @property
    def frame(self) -> int: ...

    @property
    def timestamp(self) -> ResearchNoAWorldTimestamp: ...

    def find(self, actor_id: int) -> ResearchNoAActorSnapshot | None: ...


class ResearchNoAActorSnapshot(Protocol):
    def get_velocity(self) -> CarlaVelocity: ...

    def get_transform(self) -> carla.Transform: ...


class ResearchNoAWorldTimestamp(Protocol):
    @property
    def elapsed_seconds(self) -> float: ...


class ResearchNoAWorld(Protocol):
    def get_snapshot(self) -> ResearchNoAWorldSnapshot: ...

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
        config: ResearchDriverViewConfig,
    ) -> ResearchNoAViewer: ...
