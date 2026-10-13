from __future__ import annotations

import json
from math import inf, nan

import pytest

from src.scenario.research_noa_diagnostics import (
    ResearchNoADiagnostics,
    ResearchWorldTiming,
)
from src.scenario.research_performance_summary import build_performance_summary


def test_summary_keeps_world_display_loop_and_sensor_cadences_distinct() -> None:
    diagnostics = ResearchNoADiagnostics()
    diagnostics.record_driver_loop_duration(0.1)
    diagnostics.record_driver_loop_duration(0.1)
    camera = {
        "active_drive_window": {
            "completed_display_flip_count": 6,
            "completed_display_flips_per_wall_second": 3.0,
            "roles": {
                "front": {
                    "callback_count": 10,
                    "callbacks_per_wall_second": 5.0,
                    "sensor_frames_per_sensor_second": 8.0,
                }
            },
        }
    }

    payload = json.loads(
        build_performance_summary(
            diagnostics=diagnostics,
            world=ResearchWorldTiming(100, 108, 20.0, 21.0),
            wall_elapsed_seconds=2.0,
            scheduler_updates=4,
            camera_diagnostics_json=json.dumps(camera),
            error=None,
        )
    )

    assert payload["status"] == "COMPLETE"
    assert payload["cadence"]["world_frames_per_wall_second"] == pytest.approx(4.0)
    assert payload["cadence"]["world_frames_per_sim_second"] == pytest.approx(8.0)
    assert payload["cadence"][
        "completed_driver_loops_per_wall_second"
    ] == pytest.approx(1.0)
    assert payload["cadence"][
        "completed_display_flips_per_wall_second"
    ] == pytest.approx(3.0)
    assert payload["camera"]["active_drive_window"]["roles"]["front"][
        "callbacks_per_wall_second"
    ] == pytest.approx(5.0)
    assert payload["error"] is None


def test_partial_summary_uses_none_for_unavailable_measurements() -> None:
    error = RuntimeError("viewer failed")

    payload = json.loads(
        build_performance_summary(
            diagnostics=ResearchNoADiagnostics(),
            world=None,
            wall_elapsed_seconds=0.5,
            scheduler_updates=0,
            camera_diagnostics_json=None,
            error=error,
        )
    )

    assert payload["status"] == "PARTIAL"
    assert payload["world"] is None
    assert payload["camera"] is None
    assert payload["cadence"]["world_frames_per_wall_second"] is None
    assert payload["cadence"]["completed_display_flips_per_wall_second"] is None
    assert payload["operations"]["traffic_light_actor_lookup"]["mean_ms"] is None
    assert payload["error"] == {"type": "RuntimeError", "message": "viewer failed"}


@pytest.mark.parametrize(
    ("world", "reason"),
    (
        (ResearchWorldTiming(10, 9, 1.0, 2.0), "world_frame_delta_negative"),
        (ResearchWorldTiming(10, 11, 2.0, 1.0), "simulation_elapsed_negative"),
        (ResearchWorldTiming(10, 11, nan, 2.0), "initial_simulation_nonfinite"),
        (ResearchWorldTiming(10, 11, 1.0, inf), "final_simulation_nonfinite"),
    ),
)
def test_invalid_world_measurements_produce_partial_summary(
    world: ResearchWorldTiming,
    reason: str,
) -> None:
    payload = json.loads(
        build_performance_summary(
            diagnostics=ResearchNoADiagnostics(),
            world=world,
            wall_elapsed_seconds=2.0,
            scheduler_updates=4,
            camera_diagnostics_json=None,
            error=None,
        )
    )

    assert payload["status"] == "PARTIAL"
    assert reason in payload["limitations"]["missing_measurements"]
    assert payload["cadence"]["world_frames_per_sim_second"] is None
    assert payload["cadence"]["world_frames_per_wall_second"] is None
    assert payload["cadence"]["scheduler_updates_per_wall_second"] == pytest.approx(2.0)
    assert (
        payload["world"]["frame_delta"] is None
        or payload["world"]["simulation_elapsed_seconds"] is None
    )


def test_negative_scheduler_count_has_no_rate_or_complete_claim() -> None:
    payload = json.loads(
        build_performance_summary(
            diagnostics=ResearchNoADiagnostics(),
            world=ResearchWorldTiming(10, 11, 1.0, 2.0),
            wall_elapsed_seconds=1.0,
            scheduler_updates=-1,
            camera_diagnostics_json=None,
            error=None,
        )
    )

    assert payload["status"] == "PARTIAL"
    assert payload["cadence"]["scheduler_updates_per_wall_second"] is None
    assert payload["cadence"]["scheduler_updates_per_sim_second"] is None
    assert (
        "scheduler_updates_negative" in payload["limitations"]["missing_measurements"]
    )
