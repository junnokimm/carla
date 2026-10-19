from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Protocol, assert_never

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.noa_control import NoAControlCommand
from src.experiment.noa_runtime import (
    CarlaNoARuntimeMap,
    CarlaNoARuntimeVehicle,
    NoARuntimeBundle,
)
from src.p8.tm_runtime_attestation import RuntimeAttestation
from src.scenario.lane_change_execution import (
    LaneChangeSubmission,
    LaneChangeSubmissionStatus,
    StaleLaneChangeGenerationError,
)
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_smoke import observe_live_smoke_speed_kmh
from src.scenario.traffic_manager_lane_change import (
    LaneChangeOwnershipVerifier,
    LaneChangeWorld,
    TrafficManagerLaneChangeExecutor,
)
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryAdapter

__all__ = [
    "LaneChangeSubmissionStatus",
    "StaleLaneChangeGenerationError",
    "TrafficManagerNoAControlBackend",
]


class TrafficManager(Protocol):
    def collision_detection(
        self, reference_actor, other_actor, detect_collision: bool
    ) -> None: ...

    def get_port(self) -> int: ...

    def auto_lane_change(
        self, actor: CarlaNoARuntimeVehicle, enabled: bool
    ) -> None: ...

    def random_left_lanechange_percentage(
        self, actor: CarlaNoARuntimeVehicle, percentage: float
    ) -> None: ...

    def random_right_lanechange_percentage(
        self, actor: CarlaNoARuntimeVehicle, percentage: float
    ) -> None: ...

    def update_vehicle_lights(
        self, actor: CarlaNoARuntimeVehicle, enabled: bool
    ) -> None: ...

    def ignore_lights_percentage(
        self, actor: CarlaNoARuntimeVehicle, percentage: float
    ) -> None: ...

    def force_lane_change(
        self, actor: CarlaNoARuntimeVehicle, direction: bool
    ) -> None: ...


class TrafficManagerNoAControlBackend:
    def __init__(
        self,
        vehicle: CarlaNoARuntimeVehicle,
        traffic_manager: TrafficManager,
        *,
        world: LaneChangeWorld | None = None,
        ownership_verifier: LaneChangeOwnershipVerifier | None = None,
        runtime_attestation: RuntimeAttestation | None = None,
    ) -> None:
        self._vehicle = vehicle
        self._traffic_manager = traffic_manager
        self._port = traffic_manager.get_port()
        self._active = False
        self._hazard_pairs: dict[int, object] = {}
        self.hazard_pair_revision = 0
        self._lane_changes = TrafficManagerLaneChangeExecutor(
            vehicle,
            traffic_manager,
            world,
            ownership_verifier,
            runtime_attestation,
        )
        try:
            traffic_manager.auto_lane_change(vehicle, False)
            traffic_manager.random_left_lanechange_percentage(vehicle, 0.0)
            traffic_manager.random_right_lanechange_percentage(vehicle, 0.0)
            traffic_manager.update_vehicle_lights(vehicle, False)
            self.enter_manual_control()
        except RuntimeError as error:
            try:
                vehicle.set_autopilot(False, self._port)
            except RuntimeError as cleanup_error:
                error.add_note(
                    "Traffic Manager deregistration failure: "
                    f"{type(cleanup_error).__name__}:{cleanup_error}"
                )
            raise

    @property
    def active(self) -> bool:
        return self._active

    @property
    def target_speed_kmh(self) -> None:
        return None

    def enter_noa_control(self) -> None:
        try:
            self._traffic_manager.ignore_lights_percentage(self._vehicle, 0.0)
            self._vehicle.set_autopilot(True, self._port)
            for cargo in self._hazard_pairs.values():
                self._traffic_manager.collision_detection(self._vehicle, cargo, False)
                self.hazard_pair_revision += 1
        except RuntimeError as error:
            try:
                self._vehicle.set_autopilot(False, self._port)
            except RuntimeError as cleanup_error:
                error.add_note(
                    "Traffic Manager activation rollback failure: "
                    f"{type(cleanup_error).__name__}:{cleanup_error}"
                )
            raise
        else:
            self._active = True
            self._lane_changes.activate()

    def enter_manual_control(self) -> None:
        self._lane_changes.begin_deactivation()
        self._vehicle.set_autopilot(False, self._port)
        self._lane_changes.confirm_deactivation()
        self._active = False

    def ensure_manual_control(self) -> None:
        self.enter_manual_control()

    def set_hazard_pair_detection(self, owned_cargo, enabled: bool) -> None:
        """Only a newly owned cargo pair; never changes physical collision or mode.

        New actor IDs have the TM default (True); the scene restores it before
        destruction. Static props may not enter TM's vehicle/walker pipeline:
        command return is not evidence that the planner ignores the prop.
        """
        if owned_cargo.id == self._vehicle.id:
            raise ValueError("HAZARD_PAIR_CANNOT_BE_EGO")
        self._traffic_manager.collision_detection(self._vehicle, owned_cargo, enabled)
        if enabled:
            self._hazard_pairs.pop(owned_cargo.id, None)
        else:
            self._hazard_pairs[owned_cargo.id] = owned_cargo
        self.hazard_pair_revision += 1

    @property
    def lane_change_in_flight(self) -> LaneChangeSubmission | None:
        return self._lane_changes.in_flight

    @property
    def activation_generation(self) -> int:
        return self._lane_changes.activation_generation

    def submit_lane_change(
        self,
        request_id: str,
        activation_generation: int,
        *,
        right_lane_change: bool,
    ) -> LaneChangeSubmission:
        return self._lane_changes.submit(
            request_id,
            activation_generation,
            right_lane_change=right_lane_change,
        )

    def complete_lane_change(self, generation: int) -> LaneChangeSubmission:
        return self._lane_changes.complete(generation)

    def interrupt_lane_change(self) -> LaneChangeSubmission | None:
        return self._lane_changes.interrupt()

    def invalidate_lane_change(self) -> LaneChangeSubmission | None:
        return self._lane_changes.invalidate()


class TrafficManagerScheduler:
    def __init__(
        self,
        runtime: AutomationRuntimeController,
        vehicle: CarlaNoARuntimeVehicle,
        diagnostics: ResearchNoADiagnostics | None = None,
        speed_limit_sink: Callable[[AutomationState, float, float], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self._vehicle = vehicle
        self.diagnostics = diagnostics or ResearchNoADiagnostics()
        self.update_count = 0
        self._maximum_manual_speed_kmh: float | None = None
        self._speed_limit_sink = speed_limit_sink

    @property
    def commands(self) -> tuple[NoAControlCommand, ...]:
        return ()

    @property
    def maximum_manual_speed_kmh(self) -> float | None:
        return self._maximum_manual_speed_kmh

    def update(self) -> bool:
        self.update_count += 1
        started_at = self.diagnostics.timestamp()
        try:
            speed_kmh = observe_live_smoke_speed_kmh(
                self._vehicle,
                None,
                source="TRAFFIC_MANAGER",
            )
            speed_limit_kmh = float(self._vehicle.get_speed_limit())
            if not isfinite(speed_limit_kmh):
                raise RuntimeError("Traffic Manager speed limit must be finite")
            if self._speed_limit_sink is not None:
                self._speed_limit_sink(
                    self.runtime.state,
                    speed_kmh,
                    speed_limit_kmh,
                )
            match self.runtime.state.control_mode:
                case DrivingControlMode.MANUAL:
                    self._maximum_manual_speed_kmh = (
                        speed_kmh
                        if self._maximum_manual_speed_kmh is None
                        else max(self._maximum_manual_speed_kmh, speed_kmh)
                    )
                    return False
                case DrivingControlMode.NOA_ACTIVE:
                    self.diagnostics.record_speed(speed_kmh)
                    return True
                case unreachable:
                    assert_never(unreachable)
        finally:
            self.diagnostics.record_scheduler_duration(
                self.diagnostics.timestamp() - started_at
            )


def build_traffic_manager_noa_runtime(
    vehicle: CarlaNoARuntimeVehicle,
    carla_map: CarlaNoARuntimeMap,
    traffic_manager: TrafficManager,
    *,
    world: LaneChangeWorld | None = None,
    ownership_verifier: LaneChangeOwnershipVerifier | None = None,
    runtime_attestation: RuntimeAttestation | None = None,
) -> NoARuntimeBundle:
    lane_geometry = CarlaLaneGeometryAdapter(carla_map)
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        traffic_manager,
        world=world,
        ownership_verifier=ownership_verifier,
        runtime_attestation=runtime_attestation,
    )
    runtime = AutomationRuntimeController(
        AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.MANUAL,
        ),
        backend,
    )
    return NoARuntimeBundle(
        automation_runtime=runtime,
        scheduler=None,
        control_backend=backend,
        ownership_backend=backend,
        lane_geometry=lane_geometry,
        adjacent_lanes=CarlaAdjacentLaneAdapter(carla_map),
        control_lane_geometry=lane_geometry,
        steering_target=None,
        custom_control_backend=None,
        traffic_manager_backend=backend,
    )
