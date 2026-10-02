from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_scheduler import AutomationRuntimeStateSource
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.scenario.driver_view import DriverViewConfig
from tests.test_noa_runtime import FakeMap, FakeVehicle, make_map


class FakeResearchVehicle(FakeVehicle):
    id = 42
    type_id = "vehicle.mercedes.coupe_2020"

    def __init__(self) -> None:
        super().__init__()
        self.destroy_count = 0

    def destroy(self) -> bool:
        self.destroy_count += 1
        return True


class FakeResearchMap(FakeMap):
    def __init__(self, vehicle: FakeResearchVehicle) -> None:
        composed_map = make_map(vehicle)
        super().__init__(composed_map.waypoint)
        self.name = "FakeTown04"
        self.spawn_point = carla.Transform()
        self.spawn_waypoint_requests: list[
            tuple[carla.Location, bool, carla.LaneType]
        ] = []

    def get_spawn_points(self) -> list[carla.Transform]:
        return [self.spawn_point]

    def get_waypoint(
        self,
        location: carla.Location,
        project_to_road: bool,
        lane_type: carla.LaneType,
    ):
        self.spawn_waypoint_requests.append((location, project_to_road, lane_type))
        return self.waypoint


class FakeBlueprint:
    def __init__(self) -> None:
        self.attributes: list[tuple[str, str]] = []

    def has_attribute(self, name: str) -> bool:
        return name == "role_name"

    def set_attribute(self, name: str, value: str) -> None:
        self.attributes.append((name, value))


class FakeBlueprintLibrary:
    def __init__(self, blueprint: FakeBlueprint) -> None:
        self.blueprint = blueprint
        self.requests: list[str] = []

    def find(self, identifier: str) -> FakeBlueprint:
        self.requests.append(identifier)
        return self.blueprint


class FakeResearchWorld:
    def __init__(self, vehicle: FakeResearchVehicle) -> None:
        self.vehicle = vehicle
        self.map = FakeResearchMap(vehicle)
        self.blueprint = FakeBlueprint()
        self.blueprints = FakeBlueprintLibrary(self.blueprint)
        self.spawn_calls: list[tuple[FakeBlueprint, carla.Transform]] = []

    def get_map(self) -> FakeResearchMap:
        return self.map

    def get_blueprint_library(self) -> FakeBlueprintLibrary:
        return self.blueprints

    def spawn_actor(
        self,
        blueprint: FakeBlueprint,
        transform: carla.Transform,
    ) -> FakeResearchVehicle:
        self.spawn_calls.append((blueprint, transform))
        return self.vehicle


@dataclass(frozen=True, slots=True)
class FakeClient:
    world: FakeResearchWorld

    def get_world(self) -> FakeResearchWorld:
        return self.world


class ResearchTestScheduler(Protocol):
    runtime: AutomationRuntimeStateSource

    def update(self) -> bool: ...


FrameAction = Callable[[], None]


class FakeDriverView:
    def __init__(self, config: DriverViewConfig) -> None:
        self.config = config
        self.actions: list[FrameAction] = []
        self.received_scheduler: ResearchTestScheduler | None = None
        self.attach_count = 0
        self.close_count = 0
        self.control_modes_at_run: list[DrivingControlMode] = []

    def attach(self) -> None:
        self.attach_count += 1

    def run(
        self,
        duration: float,
        scheduler: ResearchTestScheduler | None = None,
    ) -> bool:
        assert duration == 1.0
        assert scheduler is not None
        self.received_scheduler = scheduler
        self.control_modes_at_run.append(scheduler.runtime.state.control_mode)
        for action in self.actions:
            action()
            scheduler.update()
        return False

    def close(self) -> None:
        self.close_count += 1


class FakeDriverViewFactory:
    def __init__(self) -> None:
        self.created: list[FakeDriverView] = []

    def __call__(
        self,
        world: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: DriverViewConfig,
    ) -> FakeDriverView:
        assert world.vehicle is hero
        viewer = FakeDriverView(config)
        self.created.append(viewer)
        return viewer


def make_live_control_config() -> NoAControlConfig:
    return NoAControlConfig(
        longitudinal=LongitudinalControlConfig(
            target_speed_kmh=20.0,
            speed_deadband_kmh=1.0,
            acceleration_gain=0.1,
            braking_gain=0.1,
            max_throttle=0.25,
            max_brake=0.5,
        ),
        lateral=LateralControlConfig(
            lateral_error_gain=0.2,
            heading_error_gain=0.5,
            lateral_deadband_m=0.1,
            heading_deadband_rad=0.05,
            max_steering=0.15,
        ),
    )


@dataclass(frozen=True, slots=True)
class InjectedViewerError(RuntimeError):
    operation: str

    def __str__(self) -> str:
        return f"{self.operation} failed"
