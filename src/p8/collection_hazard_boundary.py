"""Resolve the gaze exclusion boundary without changing hazard execution."""
from math import isfinite


def hazard_boundary(events, *, qpc, origin_ns=None):
    """Rows include decoded payloads and both independently recorded time axes."""
    clear_road = any(
        row.get("payload", {}).get("exposure_policy") == "CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD"
        or row["event_type"] == "m2_hazard_obstacle_reveal_requested"
        for row in events
    )
    kind = "m2_hazard_display_candidate" if clear_road else "m2_hazard_preparation_started"
    policy = "FIRST_OBSTACLE_DISPLAY_TO_M2_END" if clear_road else "PREPARATION_START_TO_M2_END"
    candidates = [row for row in events if row["event_type"] == kind]
    field = "analysis_elapsed_s" if qpc else "session_elapsed_s"
    if candidates:
        row = candidates[0]
        # A missing timestamp must not be replaced by a later duplicate event.
        value = row.get(field)
        if clear_road and not qpc:
            flip = row.get("payload", {}).get("flip_completed_monotonic_ns")
            value = (flip - origin_ns) / 1e9 if isinstance(flip, int) and origin_ns is not None else None
        try:
            value = float(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            value = None
        valid = isinstance(value, (int, float)) and isfinite(value)
        return {"status": "RESOLVED" if valid else "BOUNDARY_CLOCK_UNAVAILABLE",
                "available": valid, "start_s": value if valid else None,
                "source_event": kind, "policy": policy,
                "clock_domain": "SHARED_QPC" if qpc else "PARENT_MONOTONIC"}
    observed = any(row["event_type"] in (
        "m2_hazard_obstacle_reveal_requested", "m2_hazard_geometric_exposure",
        "m2_hazard_warning_displayed", "m2_hazard_display_candidate",
    ) for row in events)
    not_occurred = not observed and any(
        row["event_type"] == "m2_hazard_not_occurred" for row in events)
    return {"status": "CONFIRMED_NOT_OCCURRED" if not_occurred else "BOUNDARY_UNAVAILABLE",
            "available": not_occurred, "start_s": None, "source_event": None,
            "policy": policy, "clock_domain": "SHARED_QPC" if qpc else "PARENT_MONOTONIC"}
