from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import (
    LongitudinalControlConfig,
    LongitudinalController,
)
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa import (
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from src.scenario.research_noa_smoke import ResearchNoAPiTracePrinter
from src.vehicle.carla_noa_simulation_control import (
    CarlaNoAControlStep,
    CarlaNoAControlTimelineError,
    SimulationTimeCarlaNoAControlBackend,
)
from tests.lateral_validation_fakes import (
    DetailedFakeLaneGeometryAdapter,
    make_geometry_context,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTimestamp,
    FakeWorldSnapshot,
    make_live_control_config,
)
from tests.test_carla_noa_control import FakeVehicle


@dataclass(frozen=True, slots=True)
class FakeControlObservation:
    frame: int
    simulation_time_seconds: float
    speed_kmh: float


def _config() -> LongitudinalControlConfig:
    return LongitudinalControlConfig(10.0, 1.0, 0.1, 0.1, 0.4, 0.5, 0.02)


def _backend(
    host_times: tuple[float, ...] = (0.0,),
) -> SimulationTimeCarlaNoAControlBackend:
    backend = SimulationTimeCarlaNoAControlBackend(
        FakeVehicle(),
        DetailedFakeLaneGeometryAdapter(LaneGeometryObservation(0.0, 0.0)),
        _config(),
        LateralControlConfig(0.2, 0.5, 0.1, 0.05, 0.15),
        control_clock=iter(host_times).__next__,
    )
    backend.enter_noa_control()
    return backend


def test_frozen_snapshot_ignores_host_advance() -> None:
    backend = _backend((0.0, 100.0))
    frozen = FakeControlObservation(100, 15.0, 9.0)

    initial = backend.step_from_observation(frozen)
    repeated = backend.step_from_observation(frozen)

    assert initial == repeated
    assert backend.last_step.delta_seconds == 0.0
    assert backend.last_step.integral_effort == 0.0


def test_same_simulation_history_is_host_rate_independent() -> None:
    history = (
        FakeControlObservation(10, 1.0, 9.0),
        FakeControlObservation(11, 1.05, 9.0),
        FakeControlObservation(12, 1.10, 9.0),
    )

    def run(host_times: tuple[float, ...]) -> tuple[NoAControlCommand, ...]:
        backend = _backend(host_times)
        return tuple(backend.step_from_observation(item) for item in history)

    assert run((0.0, 0.001, 0.002)) == run((0.0, 10.0, 100.0))


def test_new_frame_with_repeated_simulation_time_has_zero_pi_delta() -> None:
    backend = _backend()
    backend.step_from_observation(FakeControlObservation(10, 1.0, 9.0))

    backend.step_from_observation(FakeControlObservation(11, 1.0, 9.0))

    assert backend.last_step.delta_seconds == 0.0
    assert backend.last_step.integral_effort == 0.0


@pytest.mark.parametrize(
    "observation",
    [
        FakeControlObservation(9, 1.1, 9.0),
        FakeControlObservation(11, 0.9, 9.0),
    ],
)
def test_backward_snapshot_fails_before_control_side_effect(
    observation: FakeControlObservation,
) -> None:
    backend = _backend()
    backend.step_from_observation(FakeControlObservation(10, 1.0, 9.0))

    with pytest.raises(RuntimeError, match="backward"):
        backend.step_from_observation(observation)

    vehicle = backend._vehicle
    assert len(vehicle.applied_controls) == 1
    assert backend.last_step.integral_effort == 0.0


def test_manual_reentry_accepts_new_episode_time_with_zero_delta() -> None:
    backend = _backend()
    backend.step_from_observation(FakeControlObservation(100, 15.0, 9.0))
    backend.step_from_observation(FakeControlObservation(101, 15.05, 9.0))
    backend.enter_manual_control()

    backend.enter_noa_control()
    command = backend.step_from_observation(FakeControlObservation(1, 0.0, 9.0))

    assert command == NoAControlCommand(0.0, 0.0, 0.0)
    assert backend.last_step.delta_seconds == 0.0
    assert backend.last_step.integral_effort == 0.0


def test_driver_steering_override_is_capped_and_applied_once() -> None:
    backend = _backend()
    vehicle = backend._vehicle
    backend.set_driver_steering(0.8)

    command = backend.step_from_observation(FakeControlObservation(1, 0.0, 9.0))

    assert command.steering == 0.15
    assert len(vehicle.applied_controls) == 1
    assert vehicle.applied_controls[0].steer == pytest.approx(0.15)


def test_manual_reentry_clears_driver_steering_override() -> None:
    backend = _backend()
    backend.set_driver_steering(-0.8)
    backend.enter_manual_control()

    backend.enter_noa_control()
    command = backend.step_from_observation(FakeControlObservation(1, 0.0, 9.0))

    assert command.steering == 0.0


def test_requested_deadband_boundary_reproduction_records_continuous_effort() -> None:
    accelerating = LongitudinalController(_config())
    braking = LongitudinalController(_config())
    for step in range(301):
        simulation_time = step * 0.05
        accelerating.compute(9.0, control_time_seconds=simulation_time)
        braking.compute(9.0, control_time_seconds=simulation_time)

    throttle = accelerating.compute(10.999, control_time_seconds=15.02)
    brake = braking.compute(11.001, control_time_seconds=15.02)

    assert accelerating.integral_effort == pytest.approx(0.2996004)
    assert braking.integral_effort == pytest.approx(0.2995996)
    assert throttle.throttle == pytest.approx(0.2996004)
    assert throttle.brake == 0.0
    assert brake.throttle == pytest.approx(0.2994996)
    assert brake.brake == 0.0


def test_simulation_time_backend_routes_continuous_pi_effort() -> None:
    backend = _backend()
    for step in range(301):
        backend.step_from_observation(FakeControlObservation(step, step * 0.05, 9.0))

    command = backend.step_from_observation(FakeControlObservation(301, 15.02, 11.001))

    assert backend.last_step.integral_effort == pytest.approx(0.2995996)
    assert command.throttle == pytest.approx(0.2994996)
    assert command.brake == 0.0


def test_research_route_uses_one_snapshot_per_frozen_control_iteration() -> None:
    vehicle = FakeResearchVehicle()
    vehicle.velocity = type(vehicle.velocity)(9.0 / 3.6, 0.0, 0.0)
    clock = FakeMonotonicClock()
    world = FakeResearchWorld(
        vehicle,
        monotonic_clock=clock,
        snapshots=(
            FakeWorldSnapshot(100, FakeTimestamp(10.0)),
            FakeWorldSnapshot(100, FakeTimestamp(10.0)),
            FakeWorldSnapshot(100, FakeTimestamp(10.0)),
            FakeWorldSnapshot(100, FakeTimestamp(10.0)),
        ),
    )
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [
            lambda: clock.advance(10.0),
            lambda: clock.advance(20.0),
        ]
        report = session.run()

        assert report.control_frames == 2
        assert world.get_snapshot_count == 4
        assert session.bundle.control_backend.last_step is None
        assert report.commands[0] == report.commands[1]
        assert report.elapsed_seconds == pytest.approx(30.0)
        assert report.diagnostics.simulation_elapsed_seconds == pytest.approx(0.0)


def test_backward_snapshot_uses_existing_manual_cleanup_path() -> None:
    vehicle = FakeResearchVehicle()
    vehicle.velocity = type(vehicle.velocity)(9.0 / 3.6, 0.0, 0.0)
    world = FakeResearchWorld(
        vehicle,
        snapshots=(
            FakeWorldSnapshot(100, FakeTimestamp(10.0)),
            FakeWorldSnapshot(101, FakeTimestamp(10.05)),
            FakeWorldSnapshot(1, FakeTimestamp(0.0)),
        ),
    )
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with pytest.raises(CarlaNoAControlTimelineError), runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None, lambda: None]
        session.run()

    assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
    assert vehicle.destroy_count == 1


def test_pi_trace_prints_exact_step_state(capsys) -> None:
    printer = ResearchNoAPiTracePrinter()
    step = CarlaNoAControlStep(
        frame=321,
        simulation_time_seconds=12.5,
        delta_seconds=0.05,
        target_speed_kmh=10.0,
        measured_speed_kmh=9.0,
        integral_effort=0.123,
        lane_geometry=make_geometry_context(),
        command=NoAControlCommand(0.2, 0.0, -0.1),
    )

    printer(step)

    assert capsys.readouterr().out.splitlines() == [
        (
            "pi_trace,frame,simulation_time_seconds,actual_pi_dt_seconds,"
            "target_speed_kmh,measured_speed_kmh,integral_effort,"
            "commanded_throttle,commanded_brake"
        ),
        "pi_trace,321,12.5,0.05,10.0,9.0,0.123,0.2,0.0",
    ]


def test_live_pi_trace_is_opt_in_and_uses_snapshot_step(capsys) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            pi_trace=True,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        session.run()

    trace_lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("pi_trace")
    ]
    assert len(trace_lines) == 2
    assert trace_lines[1].split(",")[1:4] == ["0", "0.0", "0.0"]
