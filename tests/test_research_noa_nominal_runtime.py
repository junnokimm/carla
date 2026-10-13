from __future__ import annotations

from math import inf
from pathlib import Path

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.experiment.context import ExperimentModule
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchWorld,
    FakeSnapshotReadError,
)
from tests.test_noa_runtime import FakeVelocity
from tests.test_research_noa_live_safety import SequencedResearchVehicle
from tests.test_research_noa_nominal_policy import nominal_config


class HoldingSpeedVehicle(SequencedResearchVehicle):
    def __init__(self, speeds: list[float]) -> None:
        super().__init__([])
        self._holding_speeds = iter(speeds)
        self._last_speed = speeds[-1]

    def get_velocity(self) -> FakeVelocity:
        self.speed_read_count += 1
        self.events.append("speed")
        self._last_speed = next(self._holding_speeds, self._last_speed)
        return FakeVelocity(self._last_speed / 3.6, 0.0, 0.0)


def make_nominal_runner(
    tmp_path: Path,
    speeds: list[float],
    inputs: tuple[DriverInput, ...],
) -> tuple[ResearchNoARunner, HoldingSpeedVehicle, FakeDriverViewFactory]:
    vehicle = HoldingSpeedVehicle(speeds)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        driver_input_source=iter(inputs).__next__,
    )
    return runner, vehicle, viewer_factory


def test_noa_only_manual_speed_is_finite_but_not_numerically_capped(
    tmp_path: Path,
) -> None:
    runner, _, viewer_factory = make_nominal_runner(tmp_path, [120.0], ())
    with runner.session() as session:
        viewer_factory.created[0].actions = []

        report = session.run()

        assert report is not None
        assert report.maximum_observed_speed_kmh == pytest.approx(120.0)


def test_noa_only_manual_nonfinite_speed_fails_closed(tmp_path: Path) -> None:
    runner, _, _ = make_nominal_runner(tmp_path, [inf], ())
    with runner.session() as session:
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value.source == "INITIAL"
        assert caught.value.hard_limit_kmh is None


@pytest.mark.parametrize("speed_kmh", [110.0, 110.001])
def test_noa_only_guard_is_inclusive_and_precedes_backend_apply(
    tmp_path: Path,
    speed_kmh: float,
) -> None:
    runner, vehicle, viewer_factory = make_nominal_runner(
        tmp_path,
        [0.0, speed_kmh],
        (DriverInput(activation_requested=True),),
    )
    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        if speed_kmh == 110.0:
            report = session.run()
            assert report is not None
            assert report.control_frames == 1
        else:
            with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
                session.run()
            assert caught.value.source == "ACTIVATION_SPEED_HOLD"
            assert session.live_scheduler is not None
            assert session.live_scheduler.commands == ()
            assert all(control.throttle == 0.0 for control in vehicle.applied_controls)


def test_brake_override_changes_mode_before_mode_aware_guard(tmp_path: Path) -> None:
    runner, _, viewer_factory = make_nominal_runner(
        tmp_path,
        [0.0, 0.0, 100.0, 100.0, 120.0],
        (
            DriverInput(activation_requested=True),
            DriverInput(brake=0.2),
        ),
    )
    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None, lambda: None]

        report = session.run()

        assert report is not None
        assert report.control_frames == 1
        assert report.final_control_mode is DrivingControlMode.MANUAL
        assert report.maximum_observed_speed_kmh == pytest.approx(120.0)


def test_module_two_post_initialization_guard_runs_before_viewer(
    tmp_path: Path,
) -> None:
    vehicle = HoldingSpeedVehicle([120.0, 110.001])
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path, ExperimentModule.MODULE_2),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value.source == "ACTIVATION_SPEED_HOLD"
        assert viewer_factory.created[0].control_modes_at_run == []
        assert session.persistence is not None
        assert session.persistence.stage_started is False


def test_final_snapshot_failure_suppresses_pre_shutdown_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vehicle = HoldingSpeedVehicle([0.0])
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path, ExperimentModule.MODULE_2),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )
    callbacks = []

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        run_viewer = viewer.run

        def run_then_fail_final_snapshot(*args, **kwargs):
            result = run_viewer(*args, **kwargs)
            world.snapshot_error_at_call = world.get_snapshot_count + 1
            return result

        monkeypatch.setattr(viewer, "run", run_then_fail_final_snapshot)

        with pytest.raises(FakeSnapshotReadError):
            session.run(callbacks.append)

    assert callbacks == []


def test_noa_overspeed_at_final_observation_preserves_shutdown_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vehicle = HoldingSpeedVehicle([0.0])
    factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path, ExperimentModule.MODULE_2),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=factory,
    )
    with runner.session() as session:
        viewer = factory.created[0]
        original_run = viewer.run

        def finish_with_overspeed(*args, **kwargs):
            result = original_run(*args, **kwargs)
            vehicle._last_speed = 111.0
            return result

        monkeypatch.setattr(viewer, "run", finish_with_overspeed)
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value.source == "FINAL"
        assert caught.value.hard_limit_kmh == 110.0
        assert (
            session.bundle.automation_runtime.state.control_mode
            is DrivingControlMode.MANUAL
        )
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
    assert viewer.close_count == 1
    assert vehicle.destroy_count == 1


class TimedSpeedVehicle(HoldingSpeedVehicle):
    def __init__(self, clock: FakeMonotonicClock) -> None:
        super().__init__([0.0, 100.0])
        self._clock = clock

    def get_velocity(self) -> FakeVelocity:
        velocity = super().get_velocity()
        if self.speed_read_count == 6:
            self._clock.advance(7.0)
        return velocity


def test_active_drive_seconds_brackets_only_viewer_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeMonotonicClock()
    vehicle = TimedSpeedVehicle(clock)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        driver_input_source=iter((DriverInput(activation_requested=True),)).__next__,
        monotonic_clock=clock,
    )

    with runner.session() as session:
        assert session.interaction is not None
        initialize = session.interaction.initialize

        def initialize_with_delay(*args, **kwargs):
            clock.advance(3.0)
            return initialize(*args, **kwargs)

        monkeypatch.setattr(session.interaction, "initialize", initialize_with_delay)
        viewer_factory.created[0].actions = [lambda: clock.advance(5.0)]

        report = session.run()

        assert report is not None
        assert report.active_drive_seconds == pytest.approx(5.0)
        assert report.elapsed_seconds == pytest.approx(15.0)
