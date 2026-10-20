"""Retire an abandoned signal-guided opportunity using physical evidence.

This does not interpret a completed adjacent lane change as a completed exit,
and never expires a valid approach merely because its guidance has disappeared.
"""

OUTSIDE_CORRIDOR_HOLD_S = 2.0
OUTSIDE_CORRIDOR_MAX_GAP_S = 1.0


def retire_abandoned_exit(subject, observation):
    if not subject.config.signal_guided_exit or subject._finished:
        return
    identity = (observation.road_id, observation.section_id)
    outside = (
        subject._guidance_closed_timestamp is not None
        and all(value is not None for value in identity)
        and observation.lane_id is not None
        and not observation.route_matched
        and identity not in subject._exit_corridor_road_sections
    )
    now = observation.timestamp.host.monotonic_ns
    previous = subject._outside_exit_last_ns
    subject._outside_exit_last_ns = now
    if not outside:
        subject._outside_exit_since_ns = None
        return
    if (subject._outside_exit_since_ns is None or previous is None
            or not 0 <= now - previous <= OUTSIDE_CORRIDOR_MAX_GAP_S * 1e9):
        subject._outside_exit_since_ns = now
        return
    if now - subject._outside_exit_since_ns < OUTSIDE_CORRIDOR_HOLD_S * 1e9:
        return
    # Neither EXIT nor MISSED can be inferred from leaving the known corridor.
    # Preserve the driver's decision and record an interrupted opportunity.
    subject._emit(
        "lc_opportunity_retired", observation.timestamp,
        observation=observation, termination_reason="EXIT_CORRIDOR_LEFT",
    )
    subject._maneuver_in_progress = False
    subject._interrupt(observation.timestamp, "EXIT_CORRIDOR_LEFT")
