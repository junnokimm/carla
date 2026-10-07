from __future__ import annotations

import pytest

from src.experiment.noa_control import NoAControlCommand


def test_timing_summary_reports_means_maxima_and_cadence() -> None:
    from src.scenario.research_noa_diagnostics import (
        ResearchNoADiagnostics,
        ResearchWorldTiming,
    )

    diagnostics = ResearchNoADiagnostics()
    for duration in (0.1, 0.2, 0.3):
        diagnostics.record_driver_loop_duration(duration)
    for duration in (0.01, 0.02, 0.03):
        diagnostics.record_scheduler_duration(duration)
    for duration in (0.04, 0.05, 0.06):
        diagnostics.record_render_duration(duration)

    summary = diagnostics.summarize(
        ResearchWorldTiming(100, 500, 10.0, 30.0),
        wall_elapsed_seconds=20.0,
        scheduler_updates=60,
    )

    assert summary.initial_world_frame == 100
    assert summary.final_world_frame == 500
    assert summary.world_frame_delta == 400
    assert summary.initial_simulation_time_seconds == pytest.approx(10.0)
    assert summary.final_simulation_time_seconds == pytest.approx(30.0)
    assert summary.simulation_elapsed_seconds == pytest.approx(20.0)
    assert summary.driver_loop_iterations == 3
    assert summary.driver_loop_mean_ms == pytest.approx(200.0)
    assert summary.driver_loop_max_ms == pytest.approx(300.0)
    assert summary.scheduler_mean_ms == pytest.approx(20.0)
    assert summary.scheduler_max_ms == pytest.approx(30.0)
    assert summary.render_mean_ms == pytest.approx(50.0)
    assert summary.render_max_ms == pytest.approx(60.0)
    assert summary.scheduler_updates_per_wall_second == pytest.approx(3.0)
    assert summary.scheduler_updates_per_sim_second == pytest.approx(3.0)
    assert summary.world_frames_per_wall_second == pytest.approx(20.0)


def test_scheduler_cadence_separates_wall_and_simulation_time() -> None:
    from src.scenario.research_noa_diagnostics import (
        ResearchNoADiagnostics,
        ResearchWorldTiming,
    )

    summary = ResearchNoADiagnostics().summarize(
        ResearchWorldTiming(100, 300, 10.0, 20.0),
        wall_elapsed_seconds=20.0,
        scheduler_updates=60,
    )

    assert summary.scheduler_updates_per_wall_second == pytest.approx(3.0)
    assert summary.scheduler_updates_per_sim_second == pytest.approx(6.0)


def test_zero_or_nonfinite_elapsed_produces_no_cadence_rate() -> None:
    from src.scenario.research_noa_diagnostics import (
        ResearchNoADiagnostics,
        ResearchWorldTiming,
    )

    diagnostics = ResearchNoADiagnostics()

    summary = diagnostics.summarize(
        ResearchWorldTiming(5, 5, 1.0, 1.0),
        wall_elapsed_seconds=0.0,
        scheduler_updates=0,
    )

    assert summary.scheduler_updates_per_wall_second is None
    assert summary.scheduler_updates_per_sim_second is None
    assert summary.world_frames_per_wall_second is None


def test_active_vehicle_aggregates_are_bounded_and_exact() -> None:
    from src.scenario.research_noa_diagnostics import (
        ResearchNoADiagnostics,
        ResearchWorldTiming,
    )

    diagnostics = ResearchNoADiagnostics()
    for gear in (1, 1, 1, 2, 2):
        diagnostics.record_gear(gear)
    for speed_kmh in (0.0, 5.0, 10.0, 15.0):
        diagnostics.record_speed(speed_kmh)
    for throttle in (0.25, 0.25, 0.20, 0.10):
        diagnostics.record_command(NoAControlCommand(throttle, 0.0, 0.0))

    summary = diagnostics.summarize(
        ResearchWorldTiming(0, 0, 0.0, 0.0),
        wall_elapsed_seconds=1.0,
        scheduler_updates=4,
    )

    assert summary.active_initial_gear == 1
    assert summary.active_final_gear == 2
    assert summary.active_min_gear == 1
    assert summary.active_max_gear == 2
    assert summary.active_gear_change_count == 1
    assert summary.mean_observed_speed_kmh == pytest.approx(7.5)
    assert summary.maximum_active_speed_kmh == pytest.approx(15.0)
    assert summary.min_commanded_throttle == pytest.approx(0.10)
    assert summary.max_commanded_throttle == pytest.approx(0.25)
    assert summary.mean_commanded_throttle == pytest.approx(0.20)
    assert summary.min_commanded_brake == 0.0
    assert summary.max_commanded_brake == 0.0
    assert summary.mean_commanded_brake == 0.0
