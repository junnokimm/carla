from __future__ import annotations

import carla
import pytest

from src.experiment.automation import DrivingControlMode
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    InjectedViewerError,
    make_live_control_config,
)


def test_live_smoke_preflights_activates_and_reports_bounded_single_writer() -> None:
    from src.scenario.research_noa import (
        ResearchNoARunConfig,
        ResearchNoARunMode,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
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

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        viewer.actions = [lambda: None, lambda: None, lambda: None]

        report = session.run()

        assert viewer.control_modes_at_run == [DrivingControlMode.NOA_ACTIVE]
        assert report.spawn_index == 0
        assert report.vehicle_id == vehicle.id
        assert report.initial_lane_id == -3
        assert report.final_lane_id == -3
        assert report.scheduler_updates == 3
        assert report.control_frames == 3
        assert report.control_frames == len(vehicle.applied_controls) - 1
        assert report.user_exited is False
        assert all(command.throttle <= 0.25 for command in report.commands)
        assert all(command.brake <= 0.5 for command in report.commands)
        assert all(abs(command.steering) <= 0.15 for command in report.commands)
        assert "preflight=passed" in report.format()
        assert "control_frames=3" in report.format()
        assert "shutdown_brake=0.5" in report.format()
        assert "lane_changed=False" in report.format()
        assert "final_control_mode=manual" in report.format()
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert session.bundle.control_backend.active is False

        shutdown = vehicle.applied_controls[-1]
        assert shutdown.throttle == 0.0
        assert shutdown.brake == 0.5
        assert shutdown.steer == 0.0

    assert vehicle.autopilot_calls == [False, False, False]
    assert vehicle.destroy_count == 1


def test_live_smoke_rejects_non_driving_spawn_before_actor_or_runtime_creation() -> (
    None
):
    from src.scenario.research_noa import (
        ResearchNoAPreflightError,
        ResearchNoARunConfig,
        ResearchNoARunMode,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    world.map.waypoint.lane_type = carla.LaneType.Sidewalk
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

    with (
        pytest.raises(ResearchNoAPreflightError, match="Driving lane"),
        runner.session(),
    ):
        pass

    assert world.spawn_calls == []
    assert vehicle.autopilot_calls == []
    assert vehicle.applied_controls == []
    assert viewer_factory.created == []


def test_live_smoke_applies_bounded_shutdown_control_after_viewer_error() -> None:
    from src.scenario.research_noa import (
        ResearchNoARunConfig,
        ResearchNoARunMode,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
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

    with runner.session() as session:

        def fail_viewer() -> None:
            raise InjectedViewerError("viewer")

        viewer_factory.created[0].actions = [fail_viewer]
        with pytest.raises(InjectedViewerError, match="viewer failed"):
            session.run()

        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert len(vehicle.applied_controls) == 1
        assert vehicle.applied_controls[0].brake == 0.5


def test_public_live_deactivation_applies_one_unreported_shutdown_control() -> None:
    from src.scenario.research_noa import (
        ResearchNoARunConfig,
        ResearchNoARunMode,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
        ),
        client=FakeClient(world),
        viewer_factory=FakeDriverViewFactory(),
    )

    with runner.session() as session:
        assert session.live_scheduler is not None
        session.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        assert session.live_scheduler.update() is True

        session.request_control_mode(DrivingControlMode.MANUAL)

        assert len(session.live_scheduler.commands) == 1
        assert len(vehicle.applied_controls) == 2
        assert vehicle.applied_controls[-1].brake == 0.5
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )

    assert len(vehicle.applied_controls) == 2
