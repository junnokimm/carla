from __future__ import annotations

from dataclasses import dataclass
from math import inf

import carla
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import (
    LongitudinalControlConfig,
    LongitudinalControlValidationError,
)
from src.experiment.noa_control import NoAControlCommand
from src.vehicle.carla_noa_control import (
    CarlaNoAControlBackend,
    CarlaNoAControlUnavailableError,
    NoAControlInactiveError,
)


@dataclass(frozen=True, slots=True)
class FakeVelocity:
    x: float
    y: float
    z: float


class FakeVehicle:
    def __init__(
        self,
        velocity: FakeVelocity | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.velocity = velocity or FakeVelocity(0.0, 0.0, 0.0)
        self.operations = operations if operations is not None else []
        self.applied_controls: list[carla.VehicleControl] = []

    def get_velocity(self) -> FakeVelocity:
        self.operations.append("speed")
        return self.velocity

    def apply_control(self, control: carla.VehicleControl) -> None:
        self.operations.append("apply")
        self.applied_controls.append(control)


class LaneObservationError(RuntimeError):
    pass


class FakeLaneGeometryAdapter:
    def __init__(
        self,
        observation: LaneGeometryObservation,
        operations: list[str] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.observation = observation
        self.operations = operations if operations is not None else []
        self.fail = fail
        self.observed_vehicles: list[FakeVehicle] = []

    def observe(self, vehicle: FakeVehicle) -> LaneGeometryObservation:
        self.operations.append("lane")
        self.observed_vehicles.append(vehicle)
        if self.fail:
            raise LaneObservationError
        return self.observation


def make_longitudinal_config() -> LongitudinalControlConfig:
    return LongitudinalControlConfig(
        target_speed_kmh=36.0,
        speed_deadband_kmh=1.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
    )


def make_lateral_config() -> LateralControlConfig:
    return LateralControlConfig(
        lateral_error_gain=0.2,
        heading_error_gain=0.5,
        lateral_deadband_m=0.1,
        heading_deadband_rad=0.05,
        max_steering=0.8,
    )


def make_backend(
    vehicle: FakeVehicle | None = None,
    lane_adapter: FakeLaneGeometryAdapter | None = None,
) -> CarlaNoAControlBackend:
    actual_vehicle = vehicle if vehicle is not None else FakeVehicle()
    actual_adapter = lane_adapter or FakeLaneGeometryAdapter(
        LaneGeometryObservation(0.0, 0.0)
    )
    return CarlaNoAControlBackend(
        actual_vehicle,
        actual_adapter,
        make_longitudinal_config(),
        make_lateral_config(),
    )


def test_backend_starts_inactive() -> None:
    assert make_backend().active is False


def test_enter_noa_control_activates_backend_without_applying_control() -> None:
    vehicle = FakeVehicle()
    backend = make_backend(vehicle)

    backend.enter_noa_control()

    assert backend.active is True
    assert vehicle.applied_controls == []


def test_enter_manual_control_deactivates_without_applying_neutral_control() -> None:
    vehicle = FakeVehicle()
    backend = make_backend(vehicle)
    backend.enter_noa_control()

    backend.enter_manual_control()

    assert backend.active is False
    assert vehicle.applied_controls == []


def test_inactive_step_fails_without_applying_control() -> None:
    vehicle = FakeVehicle()
    backend = make_backend(vehicle)

    with pytest.raises(NoAControlInactiveError):
        backend.step()

    assert vehicle.operations == []
    assert vehicle.applied_controls == []


def test_active_step_reads_speed_and_observes_lane_before_applying_once() -> None:
    operations: list[str] = []
    vehicle = FakeVehicle(FakeVelocity(10.0, 0.0, 0.0), operations)
    lane_adapter = FakeLaneGeometryAdapter(
        LaneGeometryObservation(0.5, 0.2), operations
    )
    backend = make_backend(vehicle, lane_adapter)
    backend.enter_noa_control()

    backend.step()

    assert operations == ["speed", "lane", "apply"]
    assert lane_adapter.observed_vehicles == [vehicle]
    assert len(vehicle.applied_controls) == 1


def test_active_step_maps_and_returns_combined_command() -> None:
    vehicle = FakeVehicle(FakeVelocity(5.0, 0.0, 0.0))
    lane_adapter = FakeLaneGeometryAdapter(LaneGeometryObservation(0.5, 0.2))
    backend = make_backend(vehicle, lane_adapter)
    backend.enter_noa_control()

    command = backend.step()

    assert command == NoAControlCommand(throttle=0.6, brake=0.0, steering=-0.2)
    applied = vehicle.applied_controls[0]
    assert applied.throttle == pytest.approx(command.throttle)
    assert applied.brake == pytest.approx(command.brake)
    assert applied.steer == pytest.approx(command.steering)
    assert applied.hand_brake is False
    assert applied.reverse is False
    assert applied.manual_gear_shift is False


def test_active_backend_accumulates_bounded_propulsion_using_control_time() -> None:
    vehicle = FakeVehicle(FakeVelocity(35.0 / 3.6, 0.0, 0.0))
    control_times = iter((0.0, 1.0))
    config = LongitudinalControlConfig(
        target_speed_kmh=36.0,
        speed_deadband_kmh=1.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    backend = CarlaNoAControlBackend(
        vehicle,
        FakeLaneGeometryAdapter(LaneGeometryObservation(0.0, 0.0)),
        config,
        make_lateral_config(),
        control_clock=control_times.__next__,
    )
    backend.enter_noa_control()

    initial = backend.step()
    sustained = backend.step()

    assert initial.throttle == 0.0
    assert sustained.throttle > 0.0
    assert sustained.throttle <= config.max_throttle
    assert sustained.brake == 0.0


def test_manual_transition_resets_integral_effort_before_reactivation() -> None:
    vehicle = FakeVehicle(FakeVelocity(35.0 / 3.6, 0.0, 0.0))
    control_times = iter((0.0, 1.0, 2.0))
    config = LongitudinalControlConfig(
        target_speed_kmh=36.0,
        speed_deadband_kmh=1.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    backend = CarlaNoAControlBackend(
        vehicle,
        FakeLaneGeometryAdapter(LaneGeometryObservation(0.0, 0.0)),
        config,
        make_lateral_config(),
        control_clock=control_times.__next__,
    )
    backend.enter_noa_control()
    backend.step()
    learned = backend.step()
    backend.enter_manual_control()

    backend.enter_noa_control()
    reactivated = backend.step()

    assert learned.throttle > 0.0
    assert reactivated.throttle == 0.0
    assert reactivated.brake == 0.0


def test_repeated_mode_entries_are_idempotent_and_side_effect_free() -> None:
    vehicle = FakeVehicle()
    backend = make_backend(vehicle)

    backend.enter_noa_control()
    backend.enter_noa_control()
    backend.enter_manual_control()
    backend.enter_manual_control()

    assert backend.active is False
    assert vehicle.operations == []


def test_lane_geometry_failure_occurs_before_control_side_effect() -> None:
    operations: list[str] = []
    vehicle = FakeVehicle(FakeVelocity(10.0, 0.0, 0.0), operations)
    adapter = FakeLaneGeometryAdapter(
        LaneGeometryObservation(0.0, 0.0), operations, fail=True
    )
    backend = make_backend(vehicle, adapter)
    backend.enter_noa_control()

    with pytest.raises(LaneObservationError):
        backend.step()

    assert operations == ["speed", "lane"]
    assert vehicle.applied_controls == []


def test_control_computation_failure_occurs_before_control_side_effect() -> None:
    operations: list[str] = []
    vehicle = FakeVehicle(FakeVelocity(inf, 0.0, 0.0), operations)
    adapter = FakeLaneGeometryAdapter(LaneGeometryObservation(0.0, 0.0), operations)
    backend = make_backend(vehicle, adapter)
    backend.enter_noa_control()

    with pytest.raises(LongitudinalControlValidationError):
        backend.step()

    assert operations == ["speed", "lane"]
    assert vehicle.applied_controls == []


def test_unavailable_vehicle_is_rejected_at_backend_boundary() -> None:
    with pytest.raises(CarlaNoAControlUnavailableError) as caught:
        CarlaNoAControlBackend(
            None,
            FakeLaneGeometryAdapter(LaneGeometryObservation(0.0, 0.0)),
            make_longitudinal_config(),
            make_lateral_config(),
        )

    assert caught.value.resource == "vehicle"


def test_backend_satisfies_automation_runtime_transition_protocol() -> None:
    backend = make_backend()
    controller = AutomationRuntimeController(
        AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.MANUAL,
        ),
        backend,
    )

    controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    assert backend.active is True

    controller.request_control_mode(DrivingControlMode.MANUAL)
    assert backend.active is False
