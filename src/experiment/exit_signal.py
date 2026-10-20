"""Visible exit guidance, driver signal intent, and one-shot fork selection.

Lane changes always belong to the ordinary driver-signal executor. This policy
never synthesizes an indicator, submits a lane change, or changes NoA mode.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance_policy import has_timed_out, is_navigation_observation
from src.experiment.exit_assistance_types import ExitAssistanceStatus
from src.experiment.lane_change import LaneChangeDirection
from src.experiment.timestamp import TimestampEnvelope

if TYPE_CHECKING:
    from src.experiment.exit_assistance import ExitAssistanceCoordinator


def refresh_guidance(subject: ExitAssistanceCoordinator, now: TimestampEnvelope) -> None:
    if subject._t0 is None or subject._guidance_closed_timestamp is not None:
        return
    observation = subject._latest_observation
    reason = None
    if has_timed_out(subject._t0, now, subject.config.response_timeout_s):
        reason = "DISPLAY_DURATION_ELAPSED"
    elif subject.is_time_closed(now):
        reason = "ELIGIBILITY_WINDOW_CLOSED"
    elif observation is not None and not is_navigation_observation(subject.config, observation):
        reason = "APPROACH_ENDED"
    if reason is not None:
        close_guidance(subject, now, reason)


def close_guidance(subject: ExitAssistanceCoordinator, now: TimestampEnvelope, reason: str) -> None:
    if subject._guidance_closed_timestamp is not None:
        return
    subject._guidance_closed_timestamp = now
    subject.presentation = None
    if subject._decision is None and subject._actual_mode is DrivingControlMode.NOA_ACTIVE:
        subject.status = ExitAssistanceStatus.NORESPONSE
        subject._decision = "NORESPONSE"
        subject._decision_commit_timestamp = now
        subject._emit("lc_decision", now)
    subject._emit("lc_guidance_closed", now, presentation_window_status=reason)


def update_signal_exit(
    subject: ExitAssistanceCoordinator,
    driver: DriverInput,
    state: AutomationState,
    receipt: TimestampEnvelope,
    now: TimestampEnvelope,
    busy: bool,
) -> None:
    refresh_guidance(subject, receipt)
    if state.control_mode is not DrivingControlMode.NOA_ACTIVE:
        subject.cancel_automatic_route(now)
        if subject._decision == "CONFIRM":
            subject._interrupt(now, "MODE_CHANGED_AFTER_T0")
        return
    if driver.activation_requested or driver.deactivation_requested or driver.steering_engaged:
        return
    if (driver.lane_change_direction is LaneChangeDirection.RIGHT
            and subject._guidance_closed_timestamp is not None
            and subject._decision != "CONFIRM"):
        subject._emit("lc_late_indicator_ignored_for_exit", receipt, input_timestamp=receipt)
    if (subject._decision is None
            and subject._guidance_closed_timestamp is None
            and driver.exit_confirm_requested
            and driver.lane_change_direction is LaneChangeDirection.RIGHT):
        # The view supplies this edge only after a visible guidance frame. The
        # ordinary signal executor has already handled the lane-change request.
        subject.status = ExitAssistanceStatus.CONFIRMED
        subject._automated = True
        subject._decision = "CONFIRM"
        subject._input_timestamp = receipt
        subject._decision_commit_timestamp = now
        subject._emit("lc_decision", now, input_received=True, input_timestamp=receipt)
    if subject._decision == "CONFIRM" and driver.lane_change_direction is LaneChangeDirection.LEFT:
        close_guidance(subject, now, "DRIVER_LEFT_SIGNAL")
        subject._interrupt(now, "DRIVER_SIGNAL_PRIORITY")
        return
    if busy or driver.lane_change_in_progress:
        return
    _submit_branch(subject, now)


def _submit_branch(subject: ExitAssistanceCoordinator, now: TimestampEnvelope) -> None:
    observation = subject._latest_observation
    route = subject.config.route
    final_approach = next(p for p in route.points if p.lane_id == route.target_lane_id)
    if (observation is None or subject._automatic_route_armed or subject._branch_failed
            or subject._path_outcome == "EXIT" or not observation.route_matched
            or observation.route_distance_m > route.fork_distance_m
            or (observation.road_id, observation.section_id) != (final_approach.road_id, final_approach.section_id)):
        return
    if subject._decision == "CONFIRM" and observation.lane_id == route.target_lane_id:
        callback = subject._automatic_route_submission
        choice = "EXIT"
    elif subject._decision == "NORESPONSE":
        # Without an explicit straight choice TM can take the exit randomly
        # after a late ordinary right lane change. Do not suppress that input.
        callback = subject._through_route_submission
        choice = "THROUGH"
    else:
        return
    subject._automatic_route_armed = True
    subject._route_choice = choice
    try:
        accepted = callback is not None and callback()
    except (RuntimeError, ValueError, OSError):
        accepted = False
    if not accepted:
        subject._branch_failed = True
        subject.cancel_automatic_route(now)
        subject._emit("lc_route_submission_unverified", now)
        return
    subject._emit("lc_exit_route_submitted" if choice == "EXIT" else "lc_through_route_submitted", now)
