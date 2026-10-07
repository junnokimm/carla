from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.experiment.automation_scheduler import AutomationRuntimeStateSource
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.scenario.driver_view import RuntimeDriverInputObserver
from src.scenario.research_exit_runtime import ResearchExitViewBinding
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_types import ResearchPostControlObserver
from src.scenario.research_noa_view import ResearchDriverViewConfig
from tests.test_noa_runtime import FakeMap, FakeVehicle, make_map


class FakeSnapshotReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FakeBoundingBoxExtent:
    x: float = 2.0
    y: float = 0.8


@dataclass(frozen=True, slots=True)
class FakeBoundingBoxRotation:
    yaw: float = 0.0


@dataclass(frozen=True, slots=True)
class FakeBoundingBox:
    extent: FakeBoundingBoxExtent = FakeBoundingBoxExtent()
    location: FakeBoundingBoxExtent = FakeBoundingBoxExtent(0.0, 0.0)
    rotation: FakeBoundingBoxRotation = FakeBoundingBoxRotation()


class FakeResearchVehicle(FakeVehicle):
    id = 42
    type_id = "vehicle.mercedes.coupe_2020"

    def __init__(
        self,
        *,
        initial_gear: int = 1,
        reverse: bool = False,
        prime_engages_after_ticks: int | None = 1,
        gear_switch_time: float = 0.1,
    ) -> None:
        super().__init__()
        self.bounding_box = FakeBoundingBox()
        self.destroy_count = 0
        self.current_control = carla.VehicleControl(
            gear=initial_gear,
            reverse=reverse,
        )
        self.prime_engages_after_ticks = prime_engages_after_ticks
        self.physics_control = FakeVehiclePhysicsControl(gear_switch_time)
        self.get_control_count = 0
        self.confirmation_ticks = 0
        self.pending_prime_control: carla.VehicleControl | None = None
        self.events: list[str] = []

    def get_control(self) -> carla.VehicleControl:
        self.get_control_count += 1
        return self.current_control

    def get_light_state(self) -> carla.VehicleLightState:
        return carla.VehicleLightState.NONE

    def get_physics_control(self) -> FakeVehiclePhysicsControl:
        return self.physics_control

    def apply_control(self, control: carla.VehicleControl) -> None:
        self.events.append(f"control:{control.brake}")
        super().apply_control(control)
        if control.manual_gear_shift:
            self.pending_prime_control = control
            return
        control.gear = self.current_control.gear
        self.current_control = control

    def advance_control_frame(self) -> None:
        if self.pending_prime_control is None:
            return
        self.confirmation_ticks += 1
        if self.prime_engages_after_ticks is None:
            return
        if self.confirmation_ticks < self.prime_engages_after_ticks:
            return
        self.current_control = self.pending_prime_control
        self.pending_prime_control = None

    def destroy(self) -> bool:
        self.destroy_count += 1
        return True


@dataclass(frozen=True, slots=True)
class FakeVehiclePhysicsControl:
    gear_switch_time: float


class FakeMonotonicClock:
    __slots__ = ("seconds",)

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


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
    def __init__(
        self,
        vehicle: FakeResearchVehicle,
        *,
        monotonic_clock: FakeMonotonicClock | None = None,
        tick_seconds: float = 0.1,
        snapshots: tuple[FakeWorldSnapshot, ...] = (),
    ) -> None:
        self.vehicle = vehicle
        self.monotonic_clock = monotonic_clock
        self.tick_seconds = tick_seconds
        self._snapshots = iter(snapshots)
        self.wait_error: RuntimeError | None = None
        self.map = FakeResearchMap(vehicle)
        self.blueprint = FakeBlueprint()
        self.blueprints = FakeBlueprintLibrary(self.blueprint)
        self.spawn_calls: list[tuple[FakeBlueprint, carla.Transform]] = []
        self.wait_for_tick_calls: list[float] = []
        self.get_snapshot_count = 0
        self.snapshot_error_at_call: int | None = None
        self.frame = 0
        self.simulation_seconds = 0.0

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

    def get_snapshot(self) -> FakeWorldSnapshot:
        self.get_snapshot_count += 1
        self.vehicle.events.append("snapshot")
        if self.get_snapshot_count == self.snapshot_error_at_call:
            raise FakeSnapshotReadError
        fallback = FakeWorldSnapshot(self.frame, FakeTimestamp(self.simulation_seconds))
        snapshot = next(self._snapshots, fallback)
        return FakeWorldSnapshot(snapshot.frame, snapshot.timestamp, self.vehicle)

    def wait_for_tick(self, seconds: float) -> FakeWorldSnapshot:
        self.wait_for_tick_calls.append(seconds)
        if self.wait_error is not None:
            raise self.wait_error
        if self.monotonic_clock is not None:
            elapsed = min(seconds, self.tick_seconds)
            self.monotonic_clock.advance(elapsed)
            self.simulation_seconds += elapsed
        self.vehicle.advance_control_frame()
        self.frame += 1
        return FakeWorldSnapshot(self.frame)


@dataclass(frozen=True, slots=True)
class FakeTimestamp:
    elapsed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FakeWorldSnapshot:
    frame: int
    timestamp: FakeTimestamp = FakeTimestamp()
    actor: FakeResearchVehicle | None = None

    def find(self, actor_id: int) -> FakeResearchVehicle | None:
        return (
            self.actor if self.actor is not None and actor_id == self.actor.id else None
        )


@dataclass(frozen=True, slots=True)
class FakeClient:
    world: FakeResearchWorld

    def get_world(self) -> FakeResearchWorld:
        return self.world


class ResearchTestScheduler(Protocol):
    runtime: AutomationRuntimeStateSource

    def update(self) -> bool: ...


class CameraRunMeasurements(Protocol):
    initial_world_frame: int
    final_world_frame: int
    initial_simulation_seconds: float
    final_simulation_seconds: float
    host_elapsed_seconds: float
    loop_count: int


FrameAction = Callable[[], None]


class FakeDriverView:
    def __init__(self, config: ResearchDriverViewConfig) -> None:
        self.config = config
        self.actions: list[FrameAction] = []
        self.received_scheduler: ResearchTestScheduler | None = None
        self.attach_count = 0
        self.close_count = 0
        self.control_modes_at_run: list[DrivingControlMode] = []
        self.camera_diagnostics_payload: str | None = None
        self.camera_diagnostics_measurements: CameraRunMeasurements | None = None
        self.camera_diagnostics_action: (
            Callable[[CameraRunMeasurements], None] | None
        ) = None
        self.exit_view_binding: ResearchExitViewBinding | None = None

    def set_exit_view_binding(self, binding: ResearchExitViewBinding | None) -> None:
        self.exit_view_binding = binding

    def attach(self) -> None:
        self.attach_count += 1

    def capture_camera_diagnostics_environment(
        self,
        world: FakeResearchWorld,
    ) -> None:
        return

    def camera_diagnostics_json(
        self,
        measurements: CameraRunMeasurements,
    ) -> str | None:
        self.camera_diagnostics_measurements = measurements
        if self.camera_diagnostics_action is not None:
            self.camera_diagnostics_action(measurements)
        return self.camera_diagnostics_payload

    def run(
        self,
        duration: float,
        scheduler: ResearchTestScheduler | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
    ) -> bool:
        assert duration == 1.0
        assert scheduler is not None
        self.received_scheduler = scheduler
        self.control_modes_at_run.append(scheduler.runtime.state.control_mode)
        for action in self.actions:
            action()
            if input_observer is not None:
                source = self.config.driver_input_source
                input_observer.update(source() if source is not None else DriverInput())
            if diagnostics is not None:
                diagnostics.record_driver_loop_duration(0.0)
            scheduler.update()
            if isinstance(input_observer, ResearchPostControlObserver):
                input_observer.after_control_applied()
            if diagnostics is not None:
                diagnostics.record_render_duration(0.0)
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
        config: ResearchDriverViewConfig,
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
            integral_gain=0.02,
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
