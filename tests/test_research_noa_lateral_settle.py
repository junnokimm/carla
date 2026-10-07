from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.scenario.research_noa_config import ResearchNoARunConfig
from src.scenario.research_noa_lateral_validation import (
    LaneIdentity,
    LateralValidationCase,
    LateralValidationInitialCondition,
    LateralValidationInitialState,
    LateralValidationPlacement,
    LateralValidationPreflightError,
    LateralValidationVehicleMovingError,
)
from src.scenario.research_noa_lateral_validation_cli import (
    parse_lateral_validation_arguments,
    wait_for_passive_spawn_settle,
)
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext
from tests.research_noa_fakes import (
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeWorldSnapshot,
)
from tests.test_noa_runtime import FakeVelocity
from tests.test_research_noa_lateral_validation_cli import (
    _arguments,
)


class SettlingWorld(FakeResearchWorld):
    def __init__(
        self,
        vehicle: FakeResearchVehicle,
        monotonic_clock: FakeMonotonicClock,
        velocities_after_ticks: tuple[FakeVelocity, ...],
    ) -> None:
        super().__init__(vehicle, monotonic_clock=monotonic_clock, tick_seconds=0.1)
        self._velocities_after_ticks = iter(velocities_after_ticks)

    def wait_for_tick(self, seconds: float) -> FakeWorldSnapshot:
        snapshot = super().wait_for_tick(seconds)
        self.vehicle.velocity = next(
            self._velocities_after_ticks,
            self.vehicle.velocity,
        )
        return snapshot


class FrozenWorldTickError(RuntimeError):
    pass


class FrozenWorld(FakeResearchWorld):
    def wait_for_tick(self, seconds: float) -> FakeWorldSnapshot:
        self.wait_for_tick_calls.append(seconds)
        assert self.monotonic_clock is not None
        self.monotonic_clock.advance(seconds)
        raise FrozenWorldTickError


@dataclass(frozen=True, slots=True)
class SettleSession:
    world: FakeResearchWorld
    hero: FakeResearchVehicle
    config: ResearchNoARunConfig
    monotonic_clock: FakeMonotonicClock


def _placement() -> LateralValidationPlacement:
    condition = LateralValidationInitialCondition(
        LateralValidationCase.BASELINE,
        0.0,
        0.0,
    )
    return LateralValidationPlacement(
        expected_transform=FakeResearchWorld(FakeResearchVehicle()).map.spawn_point,
        condition=condition,
        original_lane_identity=LaneIdentity(36, 0, -3),
    )


def _initial_state() -> LateralValidationInitialState:
    condition = _placement().condition
    pose = PlanarPose(0.0, 0.0, 0.0)
    geometry = CarlaLaneGeometryContext(
        LaneGeometryObservation(0.0, 0.0),
        36,
        0,
        -3,
        3.5,
        pose,
        pose,
    )
    return LateralValidationInitialState(condition, geometry, 0.0, 0.5)


def _session(
    world: FakeResearchWorld,
    clock: FakeMonotonicClock,
    timeout: float,
) -> SettleSession:
    parsed = parse_lateral_validation_arguments(_arguments())
    return SettleSession(
        world,
        world.vehicle,
        replace(parsed.research_config, timeout=timeout),
        clock,
    )


def test_passive_spawn_settle_waits_for_stationary_tick_before_full_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle()
    vehicle.velocity = FakeVelocity(0.0, 0.0, 0.1)
    world = SettlingWorld(
        vehicle,
        clock,
        (FakeVelocity(0.0, 0.0, 0.0),),
    )
    expected = _initial_state()
    validation_speeds: list[float] = []

    def record_validation(*args) -> LateralValidationInitialState:
        validation_speeds.append(vehicle.velocity.z)
        return expected

    monkeypatch.setattr(
        "src.scenario.research_noa_lateral_validation_cli.validate_initial_state",
        record_validation,
    )

    result = wait_for_passive_spawn_settle(_session(world, clock, 0.5), _placement())

    assert result is expected
    assert validation_speeds == [0.0]
    assert world.wait_for_tick_calls == [pytest.approx(0.5)]
    assert vehicle.applied_controls == []


def test_passive_spawn_settle_retries_when_full_preflight_second_speed_read_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle, monotonic_clock=clock, tick_seconds=0.1)
    expected = _initial_state()
    validation_calls = 0

    def race_then_settle(*args) -> LateralValidationInitialState:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            raise LateralValidationVehicleMovingError(0.36)
        return expected

    monkeypatch.setattr(
        "src.scenario.research_noa_lateral_validation_cli.validate_initial_state",
        race_then_settle,
    )

    result = wait_for_passive_spawn_settle(_session(world, clock, 0.5), _placement())

    assert result is expected
    assert validation_calls == 2
    assert world.wait_for_tick_calls == [pytest.approx(0.5)]
    assert vehicle.applied_controls == []


def test_passive_spawn_settle_uses_host_deadline_when_vehicle_keeps_moving() -> None:
    clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle()
    vehicle.velocity = FakeVelocity(0.0, 0.0, 0.1)
    world = FakeResearchWorld(vehicle, monotonic_clock=clock, tick_seconds=0.1)

    with pytest.raises(LateralValidationPreflightError, match="stationary"):
        wait_for_passive_spawn_settle(_session(world, clock, 0.25), _placement())

    assert sum(world.wait_for_tick_calls) == pytest.approx(0.45)
    assert clock.seconds == pytest.approx(0.25)
    assert vehicle.applied_controls == []


def test_passive_spawn_settle_preserves_stationary_error_when_simulator_stops() -> None:
    clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle()
    vehicle.velocity = FakeVelocity(0.0, 0.0, 0.1)
    world = FrozenWorld(vehicle, monotonic_clock=clock)

    with pytest.raises(LateralValidationPreflightError, match="stationary"):
        wait_for_passive_spawn_settle(_session(world, clock, 0.25), _placement())

    assert world.wait_for_tick_calls == [pytest.approx(0.25)]
    assert clock.seconds == pytest.approx(0.25)
    assert vehicle.applied_controls == []
