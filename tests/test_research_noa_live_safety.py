from __future__ import annotations

from dataclasses import dataclass
from math import inf

import carla
import pytest

from src.experiment.automation import DrivingControlMode
from src.scenario.research_noa import (
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)
from tests.test_noa_runtime import FakeVelocity


class SequencedResearchVehicle(FakeResearchVehicle):
    def __init__(self, speed_samples_kmh: list[float]) -> None:
        super().__init__()
        self._speed_samples = iter(speed_samples_kmh)
        self.speed_read_count = 0

    def get_velocity(self) -> FakeVelocity:
        self.speed_read_count += 1
        speed_mps = next(self._speed_samples) / 3.6
        return FakeVelocity(speed_mps, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class LiveFixture:
    vehicle: SequencedResearchVehicle
    world: FakeResearchWorld
    viewer_factory: FakeDriverViewFactory
    runner: ResearchNoARunner


def make_live_fixture(speed_samples_kmh: list[float]) -> LiveFixture:
    vehicle = SequencedResearchVehicle(speed_samples_kmh)
    world = FakeResearchWorld(vehicle)
    world.map.name = "FakeTown04"
    world.map.spawn_point = carla.Transform(
        carla.Location(x=12.5, y=-3.25, z=0.75),
        carla.Rotation(yaw=87.5),
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
    return LiveFixture(vehicle, world, viewer_factory, runner)


def test_live_report_uses_precontrol_speed_samples_and_selected_spawn() -> None:
    fixture = make_live_fixture([0.0, 5.0, 5.0, 10.0, 10.0, 15.0, 15.0, 12.0])

    with fixture.runner.session() as session:
        viewer = fixture.viewer_factory.created[0]
        viewer.actions = [lambda: None, lambda: None, lambda: None]

        report = session.run()

        assert report.target_speed_kmh == pytest.approx(20.0)
        assert report.initial_speed_kmh == pytest.approx(0.0)
        assert report.final_speed_kmh == pytest.approx(12.0)
        assert report.maximum_observed_speed_kmh == pytest.approx(15.0)
        assert report.map_name == "FakeTown04"
        assert report.spawn_index == 0
        assert report.spawn_x == pytest.approx(12.5)
        assert report.spawn_y == pytest.approx(-3.25)
        assert report.spawn_z == pytest.approx(0.75)
        assert report.spawn_yaw == pytest.approx(87.5)
        assert report.initial_lane_id == -3
        assert report.scheduler_updates == 3
        assert report.control_frames == 3
        assert len(fixture.vehicle.applied_controls) == 4
        assert fixture.vehicle.applied_controls[-1].brake == pytest.approx(0.5)
        assert "target_speed_kmh=20.0" in report.format()
        assert "initial_speed_kmh=0.0" in report.format()
        assert "final_speed_kmh=12.0" in report.format()
        assert "maximum_observed_speed_kmh=15.0" in report.format()
        assert "map_name=FakeTown04" in report.format()
        assert "spawn_x=12.5" in report.format()

    assert fixture.vehicle.destroy_count == 1


def test_live_overspeed_aborts_before_unsafe_backend_step() -> None:
    from src.scenario import research_noa

    fixture = make_live_fixture([0.0, 5.0, 5.0, 21.0])

    with (
        pytest.raises(research_noa.ResearchNoAActualSpeedSafetyError) as caught,
        fixture.runner.session() as session,
    ):
        viewer = fixture.viewer_factory.created[0]
        viewer.actions = [lambda: None, lambda: None]
        session.run()

    assert caught.value.actual_speed_kmh == pytest.approx(21.0)
    assert caught.value.hard_limit_kmh == pytest.approx(20.0)
    assert session.live_scheduler is not None
    assert len(session.live_scheduler.commands) == 1
    assert len(fixture.vehicle.applied_controls) == 2
    assert fixture.vehicle.applied_controls[-1].brake == pytest.approx(0.5)
    assert session.bundle.automation_runtime.state.control_mode is (
        DrivingControlMode.MANUAL
    )
    assert fixture.vehicle.destroy_count == 1


def test_live_nonfinite_speed_aborts_before_backend_step() -> None:
    from src.scenario import research_noa

    fixture = make_live_fixture([0.0, inf])

    with fixture.runner.session() as session:
        viewer: FakeDriverView = fixture.viewer_factory.created[0]
        viewer.actions = [lambda: None]

        with pytest.raises(research_noa.ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value.actual_speed_kmh == inf
        assert session.live_scheduler is not None
        assert session.live_scheduler.commands == ()
        assert len(fixture.vehicle.applied_controls) == 1
        assert fixture.vehicle.applied_controls[0].brake == pytest.approx(0.5)
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )

    assert fixture.vehicle.destroy_count == 1
