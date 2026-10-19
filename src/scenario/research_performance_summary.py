from __future__ import annotations

import json
from math import isfinite

from src.scenario.research_noa_diagnostics import (
    ResearchNoADiagnostics,
    ResearchWorldTiming,
)


def build_performance_summary(
    *,
    diagnostics: ResearchNoADiagnostics,
    world: ResearchWorldTiming | None,
    wall_elapsed_seconds: float,
    scheduler_updates: int,
    camera_diagnostics_json: str | None,
    error: BaseException | None,
) -> str:
    performance = diagnostics.performance_payload()
    camera = (
        None if camera_diagnostics_json is None else json.loads(camera_diagnostics_json)
    )
    missing_measurements: list[str] = []
    if world is None:
        missing_measurements.append("world_unavailable")
    valid_wall_elapsed: float | None = wall_elapsed_seconds
    if not isfinite(valid_wall_elapsed) or valid_wall_elapsed < 0.0:
        valid_wall_elapsed = None
        missing_measurements.append("wall_elapsed_invalid")
    if scheduler_updates < 0:
        missing_measurements.append("scheduler_updates_negative")
    initial_simulation = (
        None if world is None else world.initial_simulation_time_seconds
    )
    final_simulation = None if world is None else world.final_simulation_time_seconds
    if initial_simulation is not None and not isfinite(initial_simulation):
        initial_simulation = None
        missing_measurements.append("initial_simulation_nonfinite")
    if final_simulation is not None and not isfinite(final_simulation):
        final_simulation = None
        missing_measurements.append("final_simulation_nonfinite")
    world_frame_delta = (
        None if world is None else world.final_frame - world.initial_frame
    )
    if world_frame_delta is not None and world_frame_delta < 0:
        world_frame_delta = None
        missing_measurements.append("world_frame_delta_negative")
    simulation_elapsed = (
        None
        if initial_simulation is None or final_simulation is None
        else final_simulation - initial_simulation
    )
    if simulation_elapsed is not None and simulation_elapsed < 0.0:
        simulation_elapsed = None
        missing_measurements.append("simulation_elapsed_negative")
    valid_world_window = (
        world_frame_delta is not None and simulation_elapsed is not None
    )
    active_camera = None if camera is None else camera.get("active_drive_window")
    display_rate = (
        None
        if active_camera is None
        else active_camera.get("completed_display_flips_per_wall_second")
    )
    payload = {
        "schema_version": 1,
        "status": (
            "COMPLETE"
            if error is None and world is not None and not missing_measurements
            else "PARTIAL"
        ),
        "error": (
            None
            if error is None
            else {"type": type(error).__name__, "message": str(error)}
        ),
        "world": (
            None
            if world is None
            else {
                "initial_frame": world.initial_frame,
                "final_frame": world.final_frame,
                "frame_delta": world_frame_delta,
                "initial_simulation_seconds": initial_simulation,
                "final_simulation_seconds": final_simulation,
                "simulation_elapsed_seconds": simulation_elapsed,
                "wall_elapsed_seconds": valid_wall_elapsed,
            }
        ),
        "cadence": {
            "world_frames_per_wall_second": _rate(
                world_frame_delta if valid_world_window else None, valid_wall_elapsed
            ),
            "world_frames_per_sim_second": _rate(
                world_frame_delta if valid_world_window else None, simulation_elapsed
            ),
            "completed_driver_loops_per_wall_second": _rate(
                diagnostics.completed_driver_loop_count,
                valid_wall_elapsed,
            ),
            "completed_display_flips_per_wall_second": display_rate,
            "scheduler_updates_per_wall_second": _rate(
                scheduler_updates, valid_wall_elapsed
            ),
            "scheduler_updates_per_sim_second": _rate(
                scheduler_updates, simulation_elapsed if valid_world_window else None
            ),
        },
        "operations": performance["operations"],
        "operations_by_mode": performance["operations_by_mode"],
        "operation_accumulation": performance["operation_accumulation"],
        "operation_accumulation_by_mode": performance["operation_accumulation_by_mode"],
        "mode_definition": performance["mode_definition"],
        "camera": camera,
        "overlap": {
            "driver_loop": "contains input, scheduler, control, render, display flip, and FPS limiter",
            "draw": "contains camera conversion, resize, mirror flip, blit, and HUD drawing",
            "traffic_light_observation": "contains cached traffic-light getters and conditional actor lookup",
            "map_getter": "legacy alias of waypoint_query; do not sum the two labels",
            "scheduler": "legacy scheduler and operation scheduler timing cover the same update call",
            "post_control": "contains manual_availability_refresh and persisted observation, including light_getter",
            "manual_availability_refresh": "contains activation_geometry_query, activation_bounding_box_getter and availability events; manual post-control only",
            "activation_queries": "also measured on activation checks outside post-control; do not subtract whole-run totals from post-control",
            "manual_control_delivery": "contains checked manual_batch_rpc_wait and response validation; excludes preparation and post-control",
            "input_collection": "contains indicator_input_light_rpc when a Z/X event requires a fresh observation",
            "turn_signal_audio_light_rpc": "camera.timing_by_mode; subset of audio; fresh fallback only when same-loop telemetry lights unavailable or older than 50ms",
            "turn_signal_audio_shared_light_age": "observation age, not work duration; camera.timing_by_mode count shows successful reuse",
            "event_poll_and_presentation": "contains event_poll and presentation_events; fullscreen_transition is nested in presentation_events; thread_cpu labels are not additive wall times",
            "activation_bounding_box_reused": "count of actor-local box reuse for manual availability; geometry remains fresh and actual activation refreshes box",
            "vehicle_audio_update": "subset of camera audio; cached-state model and preloaded grain launch; excludes pre-drive synthesis and exit fade",
        },
        "limitations": {
            "sensor_to_photon": "not measured",
            "summary_serialization_and_write": "excluded from captured counters",
            "missing_measurements": missing_measurements or None,
        },
    }
    return json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _rate(count: int | None, elapsed_seconds: float | None) -> float | None:
    if (
        count is None
        or count < 0
        or not isfinite(count)
        or elapsed_seconds is None
        or not isfinite(elapsed_seconds)
        or elapsed_seconds <= 0.0
    ):
        return None
    return count / elapsed_seconds
