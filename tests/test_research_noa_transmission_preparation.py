from __future__ import annotations

from dataclasses import dataclass

import carla
import pytest

from src.scenario.research_noa_transmission import (
    ResearchNoATransmissionPrimeError,
    ResearchTransmissionPrimeFailure,
    TransmissionPrimeContext,
    prime_live_transmission,
)
from tests.research_noa_fakes import FakeResearchVehicle


class PreparationTickFailure(RuntimeError):
    pass


class PreparationControlFailure(RuntimeError):
    pass


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


@dataclass(frozen=True, slots=True)
class SnapshotActor:
    speed_kmh: float

    def get_velocity(self) -> carla.Vector3D:
        return carla.Vector3D(x=self.speed_kmh / 3.6)


@dataclass(frozen=True, slots=True)
class Snapshot:
    frame: int
    actor: SnapshotActor | None

    def find(self, actor_id: int) -> SnapshotActor | None:
        return self.actor if actor_id == 42 else None


class PreparationWorld:
    def __init__(
        self,
        vehicle: FakeResearchVehicle,
        clock: FakeClock,
        snapshots: list[Snapshot],
        *,
        tick_seconds: float = 0.1,
        automatic_gear_tick: int | None = None,
        tick_error: RuntimeError | None = None,
    ) -> None:
        self.vehicle = vehicle
        self.clock = clock
        self.snapshots = snapshots
        self.tick_seconds = tick_seconds
        self.automatic_gear_tick = automatic_gear_tick
        self.tick_error = tick_error
        self.wait_for_tick_calls: list[float] = []
        self.control_counts_at_tick: list[int] = []
        self.tick_count = 0
        self.next_frame = snapshots[-1].frame + 1 if snapshots else 1

    def get_snapshot(self) -> Snapshot:
        return Snapshot(0, None)

    def wait_for_tick(self, seconds: float) -> Snapshot:
        self.wait_for_tick_calls.append(seconds)
        self.control_counts_at_tick.append(len(self.vehicle.applied_controls))
        if self.tick_error is not None:
            raise self.tick_error
        self.clock.advance(min(seconds, self.tick_seconds))
        self.tick_count += 1
        self.vehicle.advance_control_frame()
        if self.tick_count == self.automatic_gear_tick:
            self.vehicle.current_control = carla.VehicleControl(gear=1)
        if self.snapshots:
            return self.snapshots.pop(0)
        snapshot = Snapshot(self.next_frame, None)
        self.next_frame += 1
        return snapshot


class FailingPreparationControlVehicle(FakeResearchVehicle):
    def apply_control(self, control: carla.VehicleControl) -> None:
        raise PreparationControlFailure("fake preparation control failure")


class ManualAutomaticReadbackVehicle(FakeResearchVehicle):
    def apply_control(self, control: carla.VehicleControl) -> None:
        super().apply_control(control)
        if not control.manual_gear_shift and self.confirmation_ticks > 0:
            self.current_control = carla.VehicleControl(
                brake=control.brake,
                gear=1,
                manual_gear_shift=True,
            )


def context(
    clock: FakeClock,
    speed_kmh: float,
    timeout: float | None = 1.0,
) -> TransmissionPrimeContext:
    return TransmissionPrimeContext(
        bounded_brake=0.5,
        actual_speed_kmh=speed_kmh,
        monotonic_clock=clock,
        preparation_timeout_seconds=timeout,
    )


def test_reported_moving_gear_zero_settles_before_manual_prime() -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=1)
    world = PreparationWorld(
        vehicle,
        clock,
        [Snapshot(10, SnapshotActor(1.0)), Snapshot(11, SnapshotActor(0.0))],
    )

    result = prime_live_transmission(vehicle, world, context(clock, 3.905859375))

    assert result.initial_gear == 0
    assert result.applied is True
    assert result.post_prime_gear == 1
    assert world.control_counts_at_tick[:2] == [1, 1]
    assert vehicle.applied_controls[0].manual_gear_shift is False
    assert vehicle.applied_controls[1].manual_gear_shift is True
    assert vehicle.applied_controls[2].manual_gear_shift is False
    assert all(control.gear != 1 for control in vehicle.applied_controls[:1])


def test_automatic_forward_gear_during_settle_skips_manual_prime() -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0)
    world = PreparationWorld(
        vehicle,
        clock,
        [Snapshot(7, SnapshotActor(2.0)), Snapshot(8, SnapshotActor(0.0))],
        automatic_gear_tick=1,
    )

    result = prime_live_transmission(vehicle, world, context(clock, 3.905859375))

    assert result.initial_gear == 0
    assert result.applied is False
    assert result.post_prime_gear == 1
    assert world.tick_count == 2
    assert len(vehicle.applied_controls) == 1
    assert vehicle.applied_controls[0].manual_gear_shift is False


def test_stationary_threshold_boundary_does_not_apply_settling_control() -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=1)
    world = PreparationWorld(vehicle, clock, [])

    result = prime_live_transmission(vehicle, world, context(clock, 0.1))

    assert result.applied is True
    assert vehicle.applied_controls[0].manual_gear_shift is True


def test_stationary_frame_at_preparation_deadline_is_accepted() -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=1)
    world = PreparationWorld(
        vehicle,
        clock,
        [Snapshot(1, SnapshotActor(0.0))],
        tick_seconds=0.1,
    )

    result = prime_live_transmission(vehicle, world, context(clock, 1.0, 0.1))

    assert result.post_prime_gear == 1


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_preparation_timeout_fails_before_control(timeout: float) -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0)
    world = PreparationWorld(vehicle, clock, [])

    with pytest.raises(ResearchNoATransmissionPrimeError) as caught:
        prime_live_transmission(vehicle, world, context(clock, 1.0, timeout))

    assert caught.value.failure is ResearchTransmissionPrimeFailure.PREPARATION_TIMEOUT
    assert vehicle.applied_controls == []
    assert world.wait_for_tick_calls == []


@pytest.mark.parametrize(
    ("snapshots", "failure"),
    [
        (
            [Snapshot(1, SnapshotActor(1.0)), Snapshot(2, SnapshotActor(1.0))],
            "preparation-timeout",
        ),
        (
            [Snapshot(1, SnapshotActor(1.0)), Snapshot(1, SnapshotActor(0.1))],
            "preparation-frame",
        ),
        ([Snapshot(1, None)], "preparation-actor"),
        ([Snapshot(1, SnapshotActor(float("nan")))], "preparation-speed"),
    ],
)
def test_preparation_frame_failures_are_explicit(
    snapshots: list[Snapshot],
    failure: str,
) -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0)
    world = PreparationWorld(vehicle, clock, snapshots, tick_seconds=0.1)

    with pytest.raises(ResearchNoATransmissionPrimeError) as caught:
        prime_live_transmission(vehicle, world, context(clock, 1.0, 0.2))

    assert caught.value.failure.value == failure
    assert all(not control.manual_gear_shift for control in vehicle.applied_controls)


def test_preparation_tick_failure_preserves_cause() -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0)
    tick_error = PreparationTickFailure("fake preparation tick failure")
    world = PreparationWorld(vehicle, clock, [], tick_error=tick_error)

    with pytest.raises(ResearchNoATransmissionPrimeError) as caught:
        prime_live_transmission(vehicle, world, context(clock, 1.0))

    assert caught.value.failure.value == "preparation-timeout"
    assert caught.value.__cause__ is tick_error


def test_preparation_control_failure_preserves_cause() -> None:
    clock = FakeClock()
    vehicle = FailingPreparationControlVehicle(initial_gear=0)
    world = PreparationWorld(vehicle, clock, [])

    with pytest.raises(ResearchNoATransmissionPrimeError) as caught:
        prime_live_transmission(vehicle, world, context(clock, 1.0))

    assert caught.value.failure.value == "preparation-control"
    assert isinstance(caught.value.__cause__, PreparationControlFailure)


def test_preparation_callback_failure_preserves_cause() -> None:
    clock = FakeClock()
    vehicle = FakeResearchVehicle(initial_gear=0)
    world = PreparationWorld(vehicle, clock, [])
    expected = PreparationControlFailure("fake batch response failure")

    def fail_control(_control: carla.VehicleControl) -> None:
        raise expected

    prime_context = context(clock, 1.0)
    prime_context = TransmissionPrimeContext(
        bounded_brake=prime_context.bounded_brake,
        actual_speed_kmh=prime_context.actual_speed_kmh,
        monotonic_clock=prime_context.monotonic_clock,
        preparation_timeout_seconds=prime_context.preparation_timeout_seconds,
        control_sender=fail_control,
    )

    with pytest.raises(ResearchNoATransmissionPrimeError) as caught:
        prime_live_transmission(vehicle, world, prime_context)

    assert caught.value.failure.value == "preparation-control"
    assert caught.value.__cause__ is expected
    assert vehicle.applied_controls == []


def test_manual_prime_requires_automatic_forward_readback() -> None:
    clock = FakeClock()
    vehicle = ManualAutomaticReadbackVehicle(
        initial_gear=0,
        prime_engages_after_ticks=1,
    )
    world = PreparationWorld(vehicle, clock, [])

    with pytest.raises(ResearchNoATransmissionPrimeError) as caught:
        prime_live_transmission(vehicle, world, context(clock, 0.0))

    assert caught.value.failure.value == "automatic-engagement"
