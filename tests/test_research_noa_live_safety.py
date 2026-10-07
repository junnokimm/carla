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
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeSnapshotReadError,
    FakeTimestamp,
    FakeWorldSnapshot,
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
        self.events.append("speed")
        speed_mps = next(self._speed_samples) / 3.6
        return FakeVelocity(speed_mps, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class LiveFixture:
    vehicle: SequencedResearchVehicle
    world: FakeResearchWorld
    viewer_factory: FakeDriverViewFactory
    runner: ResearchNoARunner
    clock: FakeMonotonicClock


def make_live_fixture(
    speed_samples_kmh: list[float],
    *,
    camera_diagnostics: bool = False,
) -> LiveFixture:
    vehicle = SequencedResearchVehicle(speed_samples_kmh)
    clock = FakeMonotonicClock()
    world = FakeResearchWorld(
        vehicle,
        monotonic_clock=clock,
        snapshots=(
            FakeWorldSnapshot(100, FakeTimestamp(10.0)),
            FakeWorldSnapshot(101, FakeTimestamp(10.05)),
            FakeWorldSnapshot(102, FakeTimestamp(10.10)),
            FakeWorldSnapshot(103, FakeTimestamp(10.15)),
            FakeWorldSnapshot(500, FakeTimestamp(30.0)),
        ),
    )
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
            camera_diagnostics=camera_diagnostics,
            run_id="aba-a1" if camera_diagnostics else None,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
    )
    return LiveFixture(vehicle, world, viewer_factory, runner, clock)


def test_live_report_emits_opt_in_camera_json_once_before_cleanup() -> None:
    fixture = make_live_fixture(
        [0.0, 5.0, 10.0, 15.0, 12.0],
        camera_diagnostics=True,
    )

    with fixture.runner.session() as session:
        viewer = fixture.viewer_factory.created[0]
        viewer.camera_diagnostics_payload = '{"schema_version":1}'
        viewer.actions = [lambda: fixture.clock.advance(20.0 / 3.0)] * 3
        modes_at_serialization: list[DrivingControlMode] = []
        snapshot_counts_at_serialization: list[int] = []

        def serialize_after_final_measurements(_measurements) -> None:
            modes_at_serialization.append(
                session.bundle.automation_runtime.state.control_mode
            )
            snapshot_counts_at_serialization.append(fixture.world.get_snapshot_count)
            fixture.clock.advance(100.0)

        viewer.camera_diagnostics_action = serialize_after_final_measurements

        report = session.run()

        assert report.format().count("camera_diagnostics_json=") == 1
        assert report.camera_diagnostics_json == '{"schema_version":1}'
        assert modes_at_serialization == [DrivingControlMode.MANUAL]
        assert snapshot_counts_at_serialization == [5]
        assert report.elapsed_seconds == pytest.approx(20.0)
        assert report.diagnostics.initial_world_frame == 100
        assert report.diagnostics.final_world_frame == 500
        assert report.diagnostics.simulation_elapsed_seconds == pytest.approx(20.0)
        assert viewer.camera_diagnostics_measurements is not None
        assert (
            viewer.camera_diagnostics_measurements.host_elapsed_seconds
            == pytest.approx(20.0)
        )
        assert viewer.camera_diagnostics_measurements.loop_count == 3
        assert len(fixture.vehicle.applied_controls) == 4
        assert fixture.vehicle.applied_controls[-1].brake == pytest.approx(0.5)
        assert viewer.close_count == 0

    assert viewer.close_count == 1


def test_live_report_uses_precontrol_speed_samples_and_selected_spawn() -> None:
    fixture = make_live_fixture([0.0, 5.0, 10.0, 15.0, 12.0])

    with fixture.runner.session() as session:
        viewer = fixture.viewer_factory.created[0]
        viewer.actions = [lambda: fixture.clock.advance(20.0 / 3.0)] * 3

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
        assert report.diagnostics.initial_world_frame == 100
        assert report.diagnostics.final_world_frame == 500
        assert report.diagnostics.world_frame_delta == 400
        assert report.diagnostics.simulation_elapsed_seconds == pytest.approx(20.0)
        assert report.diagnostics.driver_loop_iterations == 3
        assert report.diagnostics.scheduler_updates_per_wall_second == pytest.approx(
            0.15
        )
        assert report.diagnostics.scheduler_updates_per_sim_second == pytest.approx(
            0.15
        )
        assert report.diagnostics.active_initial_gear == 1
        assert report.diagnostics.active_final_gear == 1
        assert report.diagnostics.mean_observed_speed_kmh == pytest.approx(10.0)
        assert report.diagnostics.mean_commanded_throttle == pytest.approx(0.25)
        assert report.diagnostics.mean_applied_throttle == pytest.approx(0.25)
        assert report.diagnostics.max_applied_brake == pytest.approx(0.0)
        assert report.diagnostics.active_hand_brake_seen is False
        assert report.diagnostics.active_reverse_seen is False
        assert report.diagnostics.active_manual_gear_shift_seen is False
        assert fixture.world.get_snapshot_count == 5
        assert fixture.world.wait_for_tick_calls == []
        assert fixture.vehicle.events[-3:] == ["speed", "control:0.5", "snapshot"]
        assert len(fixture.vehicle.applied_controls) == 4
        assert fixture.vehicle.applied_controls[-1].brake == pytest.approx(0.5)
        assert "target_speed_kmh=20.0" in report.format()
        assert "initial_speed_kmh=0.0" in report.format()
        assert "final_speed_kmh=12.0" in report.format()
        assert "maximum_observed_speed_kmh=15.0" in report.format()
        assert "simulation_elapsed_seconds=20.0" in report.format()
        assert "driver_loop_iterations=3" in report.format()
        assert "active_initial_gear=1" in report.format()
        assert "mean_commanded_throttle=0.25" in report.format()
        assert "mean_applied_throttle=0.25" in report.format()
        assert "max_applied_brake=0.0" in report.format()
        assert "active_hand_brake_seen=False" in report.format()
        assert "active_reverse_seen=False" in report.format()
        assert "active_manual_gear_shift_seen=False" in report.format()
        assert "map_name=FakeTown04" in report.format()
        assert "spawn_x=12.5" in report.format()

    assert fixture.vehicle.destroy_count == 1


def test_live_overspeed_aborts_before_unsafe_backend_step() -> None:
    from src.scenario import research_noa

    fixture = make_live_fixture([0.0, 5.0, 21.0])

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


def test_final_snapshot_failure_still_deactivates_and_brakes() -> None:
    fixture = make_live_fixture([0.0, 5.0, 5.0, 5.0])
    fixture.world.snapshot_error_at_call = 3

    with fixture.runner.session() as session:
        viewer = fixture.viewer_factory.created[0]
        viewer.actions = [lambda: None]

        with pytest.raises(FakeSnapshotReadError):
            session.run()

        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert fixture.world.get_snapshot_count == 3
        assert len(fixture.vehicle.applied_controls) == 2
        assert fixture.vehicle.applied_controls[-1].brake == pytest.approx(0.5)
