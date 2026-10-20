"""Read-only condition evidence. No RPC, input, vehicle or scheduler writes."""

from copy import deepcopy

from src.p7.moving_config import WINDOW
from src.p8.exit_before_hazard import sequence_evidence


def readiness_record(scene, sample, blocker, reason, preflight_complete):
    evidence = deepcopy(scene.backend.preparation_attempt)
    checks = evidence["checks"]
    checks["exit_before_hazard"] = sequence_evidence(scene.session.exit_assistance)
    checks["exit_conflict_free"] = (
        {"status": "PASS"} if blocker is None
        else {"status": "FAIL", "reason": blocker} if blocker.startswith("P5")
        else {"status": "NOT_CHECKED", "reason": blocker}
    )
    checks["driving_condition_clear"] = (
        {"status": "FAIL", "reason": blocker}
        if blocker in ("ROAD_DEPARTURE_PROXY_ACTIVE", "COLLISION_OBSERVED_THIS_SAMPLE")
        else {"status": "PASS"}
    )
    checks["exposure_window"] = {
        "status": "PASS" if WINDOW[0] <= scene.elapsed <= WINDOW[1] else "FAIL",
        "window_s": list(WINDOW),
    }
    checks["planned_time_reached"] = {
        "status": "PASS" if scene.elapsed >= scene.planned else "FAIL",
        "planned_exposure_active_s": scene.planned,
    }
    held = (
        max(0.0, scene.elapsed - max(scene.stable_since, scene.clear_since))
        if preflight_complete and scene.stable_since is not None and scene.clear_since is not None
        else None
    )
    checks["stable_speed_and_clear_road"] = {
        "status": "NOT_CHECKED" if held is None
        else "PASS" if held >= scene.config.stable_speed_s else "FAIL",
        "observed_hold_s": held, "required_hold_s": scene.config.stable_speed_s,
        "reason": reason if held is None else None,
    }
    coordinator = scene.session.exit_assistance
    return {
        **evidence,
        "reason": reason,
        "session_action": "CONTINUE",
        "observation_monotonic_ns": sample.timestamp.host.monotonic_ns,
        "observation_frame": sample.timestamp.carla_snapshot.frame,
        "m2_active_elapsed_s": scene.elapsed,
        "exit_status": None if coordinator is None else coordinator.status.value,
        "exit_decision": None if coordinator is None else getattr(coordinator, "_decision", None),
        "exit_route_armed": False if coordinator is None else getattr(coordinator, "_automatic_route_armed", False),
    }
