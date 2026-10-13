from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.experiment.automation_scheduler import AutomationRuntimeStateSource
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.p8.tm_runtime_attestation import RuntimeAttestation
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
        self.autopilot_port_calls: list[tuple[bool, int | None]] = []
        self.current_light_state = carla.VehicleLightState.NONE
        self.speed_limit_kmh = 80.0
        self.at_traffic_light = False
        self.traffic_light = None

    def set_autopilot(self, enabled: bool, tm_port: int | None = None) -> None:
        self.events.append(f"autopilot:{enabled}")
        self.autopilot_port_calls.append((enabled, tm_port))
        super().set_autopilot(enabled)

    def get_control(self) -> carla.VehicleControl:
        self.get_control_count += 1
        return self.current_control

    def get_light_state(self) -> carla.VehicleLightState:
        return self.current_light_state

    def set_light_state(self, state: carla.VehicleLightState) -> None:
        self.current_light_state = state

    def get_speed_limit(self) -> float:
        return self.speed_limit_kmh

    def is_at_traffic_light(self) -> bool:
        return self.at_traffic_light

    def get_traffic_light(self):
        return self.traffic_light

    def get_traffic_light_state(self) -> carla.TrafficLightState:
        if self.traffic_light is None:
            return carla.TrafficLightState.Green
        return self.traffic_light.state

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
        return name in {"role_name", "sticky_control"}

    def set_attribute(self, name: str, value: str) -> None:
        self.attributes.append((name, value))


class FakeBlueprintLibrary:
    def __init__(self, blueprint: FakeBlueprint) -> None:
        self.blueprint = blueprint
        self.requests: list[str] = []

    def find(self, identifier: str) -> FakeBlueprint:
        self.requests.append(identifier)
        return self.blueprint


class FakeActorList(tuple[FakeResearchVehicle, ...]):
    def filter(self, pattern: str) -> FakeActorList:
        prefix = pattern.removesuffix("*")
        return FakeActorList(
            actor for actor in self if actor.type_id.startswith(prefix)
        )


class FakeResearchWorld:
    def __init__(
        self,
        vehicle: FakeResearchVehicle,
        *,
        monotonic_clock: FakeMonotonicClock | None = None,
        tick_seconds: float = 0.1,
        snapshots: tuple[FakeWorldSnapshot, ...] = (),
        synchronous_mode: bool = False,
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
        self.synchronous_mode = synchronous_mode

    def get_settings(self) -> FakeWorldSettings:
        return FakeWorldSettings(self.synchronous_mode)

    def get_map(self) -> FakeResearchMap:
        return self.map

    def get_blueprint_library(self) -> FakeBlueprintLibrary:
        return self.blueprints

    def get_actors(self) -> FakeActorList:
        return FakeActorList((self.vehicle,))

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
class FakeWorldSettings:
    synchronous_mode: bool = False


@dataclass(frozen=True, slots=True)
class FakeWorldSnapshot:
    frame: int
    timestamp: FakeTimestamp = FakeTimestamp()
    actor: FakeResearchVehicle | None = None

    def find(self, actor_id: int) -> FakeResearchVehicle | None:
        return (
            self.actor if self.actor is not None and actor_id == self.actor.id else None
        )


class FakeTrafficManager:
    def __init__(self, port: int = 8000) -> None:
        self.port = port
        self.calls: list[tuple[str, int | bool | float]] = []

    def get_port(self) -> int:
        self.calls.append(("get_port", self.port))
        return self.port

    def auto_lane_change(self, actor: FakeResearchVehicle, enabled: bool) -> None:
        self.calls.append(("auto_lane_change", enabled))

    def random_left_lanechange_percentage(
        self, actor: FakeResearchVehicle, percentage: float
    ) -> None:
        self.calls.append(("random_left_lanechange_percentage", percentage))

    def random_right_lanechange_percentage(
        self, actor: FakeResearchVehicle, percentage: float
    ) -> None:
        self.calls.append(("random_right_lanechange_percentage", percentage))

    def update_vehicle_lights(self, actor: FakeResearchVehicle, enabled: bool) -> None:
        self.calls.append(("update_vehicle_lights", enabled))

    def ignore_lights_percentage(
        self, actor: FakeResearchVehicle, percentage: float
    ) -> None:
        self.calls.append(("ignore_lights_percentage", percentage))
        actor.events.append(f"tm:ignore_lights_percentage:{percentage}")

    def force_lane_change(self, actor: FakeResearchVehicle, direction: bool) -> None:
        self.calls.append(("force_lane_change", direction))


class FakeTrafficManagerRuntimeAttestor:
    def __init__(self) -> None:
        self.attestation = RuntimeAttestation(
            8000,
            1,
            Path("fake-libcarla.pyd"),
            "FAKE",
        )
        self.calls: list[tuple[str, int | object]] = []

    def verify_before_creation(self, port: int) -> object:
        self.calls.append(("before", port))
        return self.attestation

    def attest_after_creation(self, prepared: object) -> object:
        self.calls.append(("after", prepared))
        return self.attestation

    def verify_owned(self, attestation: RuntimeAttestation) -> None:
        self.calls.append(("owned", attestation))


@dataclass(frozen=True, slots=True)
class FakeCommandResponse:
    actor_id: int
    error: str = ""

    def has_error(self) -> bool:
        return bool(self.error)


@dataclass(frozen=True, slots=True)
class FakeClient:
    world: FakeResearchWorld
    traffic_manager: FakeTrafficManager | None = None
    batch_calls: list[tuple[tuple[carla.command.ApplyVehicleControl, ...], bool]] = (
        field(default_factory=list)
    )
    batch_errors: list[RuntimeError] = field(default_factory=list)

    def get_world(self) -> FakeResearchWorld:
        return self.world

    def get_trafficmanager(self, port: int) -> FakeTrafficManager:
        traffic_manager = self.traffic_manager or FakeTrafficManager(port)
        if traffic_manager.port != port:
            raise AssertionError("unexpected Traffic Manager port")
        return traffic_manager

    def get_client_version(self) -> str:
        return "0.9.16-test-client"

    def get_server_version(self) -> str:
        return "0.9.16-test-server"

    def apply_batch_sync(
        self,
        commands: list[carla.command.ApplyVehicleControl],
        do_tick: bool = False,
    ) -> list[FakeCommandResponse]:
        if self.batch_errors:
            raise self.batch_errors.pop(0)
        self.batch_calls.append((tuple(commands), do_tick))
        responses: list[FakeCommandResponse] = []
        for command in commands:
            control = command.control
            self.world.vehicle.apply_control(
                carla.VehicleControl(
                    throttle=control.throttle,
                    steer=control.steer,
                    brake=control.brake,
                    hand_brake=control.hand_brake,
                    reverse=control.reverse,
                    manual_gear_shift=control.manual_gear_shift,
                    gear=control.gear,
                )
            )
            responses.append(FakeCommandResponse(command.actor_id))
        return responses


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
        self.training_completed_early = False

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

    def freeze_performance_measurement_window(self) -> None:
        return

    def run(
        self,
        duration: float,
        scheduler: ResearchTestScheduler | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
        input_observer: RuntimeDriverInputObserver | None = None,
        continuation_factory=None,
    ) -> bool:
        duration_cap = 780.0
        assert 0.0 < duration <= duration_cap
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
        if continuation_factory is not None:
            continuation = continuation_factory()
            if continuation is not None:
                continuation.start()
                try:
                    active = True
                    while active:
                        source = self.config.driver_input_source
                        driver_input = source() if source is not None else DriverInput()
                        if input_observer is not None:
                            input_observer.update(driver_input)
                        scheduler.update()
                        if isinstance(input_observer, ResearchPostControlObserver):
                            input_observer.after_control_applied()
                        active = continuation.after_control_applied(driver_input)
                        if diagnostics is not None:
                            diagnostics.record_render_duration(0.0)
                    continuation.finalize("TERMINAL")
                except BaseException as error:
                    continuation.finalize("ERROR", error)
                    raise
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
