from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance_event import ExitEventData, build_exit_event
from src.experiment.exit_assistance_observation import observe_exit
from src.experiment.exit_assistance_policy import (
    draft_presentation,
    has_timed_out,
    is_automated_branch,
    is_confirmation_observation,
    is_navigation_observation,
    lane_change_is_in_progress,
)
from src.experiment.exit_assistance_types import (
    ExitAssistanceConfig,
    ExitAssistanceEvent,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
    ExitPresentation,
    ExitRouteReference,
    ExitTerminationReason,
)
from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import TimestampEnvelope

__all__ = [
    "ExitAssistanceConfig",
    "ExitAssistanceCoordinator",
    "ExitAssistanceEvent",
    "ExitAssistanceStatus",
    "ExitContext",
    "ExitObservation",
    "ExitPresentation",
    "ExitPresentationError",
    "ExitTerminationReason",
]


# allow: SIZE_OK - lifecycle transitions stay co-located for exhaustive safety review.
class ExitAssistanceCoordinator:
    """Own the single mutable lifecycle for one physical exit opportunity."""

    def __init__(
        self,
        config: ExitAssistanceConfig,
        reference: ExitRouteReference,
        event_sink: Callable[[ExitAssistanceEvent], None] | None = None,
        *,
        session_clock: SessionClock | None = None,
        assisted_submission: Callable[[], bool] | None = None,
        submission_clock: Callable[[], TimestampEnvelope] | None = None,
        automatic_route_submission: Callable[[], bool] | None = None,
        automatic_route_cancellation: Callable[[], bool] | None = None,
        through_route_submission: Callable[[], bool] | None = None,
    ) -> None:
        if assisted_submission is not None and submission_clock is None:
            raise ExitPresentationError("TM submission requires its observation clock")
        self.config = config
        self._reference = reference
        self.status = ExitAssistanceStatus.WAITING
        self.presentation: ExitPresentation | None = None
        self.events: list[ExitAssistanceEvent] = []
        self._event_sink = event_sink
        self._t0: TimestampEnvelope | None = None
        # Physical resolution evidence, independent of file logging success.
        # A timeout or a cancelled assistance request is not a passed exit.
        self._opportunity_end_timestamp: TimestampEnvelope | None = None
        self._opportunity_end_outcome: str | None = None
        self._opportunity_end_reason: str | None = None
        self._frozen_mode: DrivingControlMode | None = None
        self._actual_mode: DrivingControlMode | None = None
        self._automated = False
        self._recommendation_presented = False
        self._finished = False
        self._decision: str | None = None
        self._input_timestamp: TimestampEnvelope | None = None
        self._decision_commit_timestamp: TimestampEnvelope | None = None
        self._observed_proxy_timestamp: TimestampEnvelope | None = None
        self._indicator_timestamp: TimestampEnvelope | None = None
        self._steering_timestamp: TimestampEnvelope | None = None
        self._lateral_timestamp: TimestampEnvelope | None = None
        self._crossing_timestamp: TimestampEnvelope | None = None
        self._input_was_held = False
        self._last_observation_frame: int | None = None
        self._latest_observation: ExitObservation | None = None
        self._baseline_lateral_m: float | None = None
        self._baseline_indicator = "off"
        self._previous_indicator = "off"
        self._previous_lateral_m: float | None = None
        self._maneuver_in_progress = False
        self._path_outcome: str | None = None
        self._unpresented_path_identity: tuple[int, int, int] | None = None
        self._route_was_armed = False
        self._session_clock = session_clock
        self._assisted_submission = assisted_submission
        self._submission_clock = submission_clock
        self._active_drive_start: TimestampEnvelope | None = None
        self._active_drive_start_elapsed_s: float | None = None
        self.logging_errors: list[str] = []
        self._automatic_route_submission = automatic_route_submission
        self._automatic_route_cancellation = automatic_route_cancellation
        self._automatic_route_armed = False
        self._through_route_submission = through_route_submission
        self._guidance_closed_timestamp: TimestampEnvelope | None = None
        self._guidance_hidden_timestamp: TimestampEnvelope | None = None
        self._route_choice: str | None = None
        self._branch_failed = False
        self._outside_exit_since_ns = None
        self._outside_exit_last_ns = None
        self._exit_corridor_road_sections = frozenset(
            [(point.road_id, point.section_id) for point in config.route.points]
            + [segment[:2] for segment in (
                *config.route.exit_segments, *config.route.through_segments
            )]
        )
        self._automatic_route_cancel_unverified = False

    def start_active_drive(self, timestamp: TimestampEnvelope) -> None:
        if self._active_drive_start is not None:
            raise ExitPresentationError("active drive timing is already started")
        self._active_drive_start = timestamp
        self._active_drive_start_elapsed_s = (
            None
            if self._session_clock is None
            else self._session_clock.project_elapsed_seconds(timestamp.host)
        )
        self._emit("lc_active_drive_started", timestamp)

    def eligibility_elapsed_seconds(self, timestamp: TimestampEnvelope) -> float | None:
        if self._active_drive_start is None:
            return None
        if self._session_clock is None:
            return (
                timestamp.host.monotonic_ns - self._active_drive_start.host.monotonic_ns
            ) / 1_000_000_000.0
        start = self._active_drive_start_elapsed_s
        if start is None:
            raise ExitPresentationError("active drive SessionClock origin is missing")
        return self._session_clock.project_elapsed_seconds(timestamp.host) - start

    def is_time_eligible(self, timestamp: TimestampEnvelope) -> bool:
        if self.config.eligibility_open_s is None:
            return True
        elapsed = self.eligibility_elapsed_seconds(timestamp)
        if elapsed is None:
            raise ExitPresentationError("timed exit requires active drive start")
        close = self.config.eligibility_close_s
        if close is None:
            raise ExitPresentationError("timed exit close is missing")
        return self.config.eligibility_open_s <= elapsed <= close

    def is_time_closed(self, timestamp: TimestampEnvelope) -> bool:
        close = self.config.eligibility_close_s
        if close is None:
            return False
        elapsed = self.eligibility_elapsed_seconds(timestamp)
        if elapsed is None:
            raise ExitPresentationError("timed exit requires active drive start")
        return elapsed > close

    def mark_not_presented(self, timestamp: TimestampEnvelope) -> None:
        self.status = ExitAssistanceStatus.NOT_PRESENTED
        self.presentation = None
        self._emit(
            "lc_finished",
            timestamp,
            outcome="NOT_PRESENTED",
            termination_reason="ELIGIBILITY_WINDOW_CLOSED",
        )

    def observe(self, observation: ExitObservation) -> None:
        observe_exit(self, observation)

    @property
    def lane_change_in_progress(self) -> bool:
        return lane_change_is_in_progress(self.status, self._maneuver_in_progress)

    def prepare_presentation(self, state: AutomationState) -> ExitPresentation:
        observation = self._latest_observation
        if observation is None or self.presentation is None:
            raise ExitPresentationError("exit presentation is not ready")
        if self.status is not ExitAssistanceStatus.DRAFT:
            if self.config.signal_guided_exit:
                return replace(
                    self.presentation,
                    recommendation=(
                        self._guidance_closed_timestamp is None
                        and state.control_mode is DrivingControlMode.NOA_ACTIVE
                        and is_navigation_observation(self.config, observation)
                    ),
                )
            return replace(
                self.presentation,
                recommendation=(
                    self.presentation.recommendation
                    and self._lateral_timestamp is None
                    and is_confirmation_observation(self.config, observation)
                ),
            )
        remaining = self.config.route.fork_distance_m - observation.route_distance_m
        self.presentation = draft_presentation(
            self.config,
            remaining,
            is_automated_branch(self.config, state.control_mode)
            and (is_navigation_observation(self.config, observation)
                 if self.config.signal_guided_exit
                 else is_confirmation_observation(self.config, observation)),
            observation.carla_frame,
            observation.route_point_index,
        )
        return self.presentation

    def mark_presented(
        self,
        rendered: ExitPresentation,
        state: AutomationState,
        timestamp: TimestampEnvelope,
    ) -> None:
        presentation_window_status = (
            "WITHIN_WINDOW"
            if self.is_time_eligible(timestamp)
            else "OUTSIDE_WINDOW_AFTER_VISIBLE_FRAME"
        )
        observation = self._latest_observation
        if (
            self.status is not ExitAssistanceStatus.DRAFT
            or observation is None
            or rendered != self.presentation
            or rendered.observation_frame != observation.carla_frame
            or rendered.route_point_index != observation.route_point_index
        ):
            raise ExitPresentationError("rendered exit presentation is stale")
        if not is_navigation_observation(self.config, observation) or (
            rendered.recommendation
            and not (is_navigation_observation(self.config, observation)
                     if self.config.signal_guided_exit
                     else is_confirmation_observation(self.config, observation))
        ):
            raise ExitPresentationError("vehicle is outside exit presentation envelope")
        self.status = ExitAssistanceStatus.PRESENTED
        self._t0 = timestamp
        self._frozen_mode = state.control_mode
        self._actual_mode = state.control_mode
        self._automated = is_automated_branch(self.config, state.control_mode)
        self._recommendation_presented = rendered.recommendation
        self._baseline_lateral_m = (
            observation.lateral_offset_m if observation.target_boundary_valid else None
        )
        self._baseline_indicator = observation.indicator
        self._previous_indicator = observation.indicator
        self._previous_lateral_m = self._baseline_lateral_m
        self.presentation = rendered
        self._emit(
            "lc_presented",
            timestamp,
            observation=observation,
            presentation_window_status=presentation_window_status,
        )
        if presentation_window_status == "OUTSIDE_WINDOW_AFTER_VISIBLE_FRAME":
            self._emit(
                "lc_presentation_timing_deviation",
                timestamp,
                presentation_window_status=presentation_window_status,
            )

    def update(
        self,
        driver_input: DriverInput,
        state: AutomationState,
        receipt_timestamp: TimestampEnvelope,
        commit_timestamp: TimestampEnvelope | None = None,
        *,
        automatic_lane_change_busy: bool = False,
    ) -> None:
        commit = receipt_timestamp if commit_timestamp is None else commit_timestamp
        if self._finished:
            return
        previous_mode = self._actual_mode
        self._actual_mode = state.control_mode
        requested = (
            driver_input.exit_confirm_requested or driver_input.exit_reject_requested
        )
        fresh_request = requested and not self._input_was_held
        self._input_was_held = requested
        if (
            self._t0 is not None
            and previous_mode is not None
            and state.control_mode is not previous_mode
        ):
            self._emit("lc_mode_change", commit)
        if self.config.signal_guided_exit:
            if self._t0 is not None and self.status is not ExitAssistanceStatus.COMPLETED:
                from src.experiment.exit_signal import update_signal_exit

                update_signal_exit(
                    self, driver_input, state, receipt_timestamp, commit,
                    automatic_lane_change_busy,
                )
            return
        if self.status not in (
            ExitAssistanceStatus.PRESENTED,
            ExitAssistanceStatus.CONFIRMED,
        ):
            return
        if (
            self.status is ExitAssistanceStatus.PRESENTED
            and self._automated
            and self._recommendation_presented
            and has_timed_out(self._t0, commit, self.config.response_timeout_s)
        ):
            self.status = ExitAssistanceStatus.NORESPONSE
            self._decision = "NORESPONSE"
            self._decision_commit_timestamp = commit
            self._emit("lc_decision", commit)
            return
        if driver_input.steering_engaged and self._steering_timestamp is None:
            self._steering_timestamp = receipt_timestamp
            self._emit("lc_driver_steering_onset", receipt_timestamp)
        if (
            self.status is ExitAssistanceStatus.PRESENTED
            and driver_input.activation_requested
            and driver_input.steering_engaged
        ):
            self._interrupt(commit, "DRIVER_PRIORITY_OR_MOVEMENT")
            return
        if (
            self.status is ExitAssistanceStatus.PRESENTED
            and fresh_request
            and (
                driver_input.brake > 0.0
                or driver_input.steering_engaged
                or driver_input.lane_change_in_progress
            )
        ):
            self.status = ExitAssistanceStatus.INTERRUPTED
            self._decision = "UNAVAILABLE_DRIVER_PRIORITY"
            self._input_timestamp = receipt_timestamp
            self._decision_commit_timestamp = commit
            self._emit("lc_decision", commit)
            return
        if state.control_mode is not self._frozen_mode:
            self._interrupt(commit, "MODE_CHANGED_AFTER_T0")
            return
        if self.status is ExitAssistanceStatus.CONFIRMED and (
            driver_input.brake > 0.0
            or driver_input.steering_engaged
            or driver_input.lane_change_in_progress
        ):
            self._interrupt(commit, "DRIVER_PRIORITY_OR_MOVEMENT")
            return
        if not fresh_request or self.status is not ExitAssistanceStatus.PRESENTED:
            return
        if not self._recommendation_presented or not self._automated:
            return
        if driver_input.exit_reject_requested:
            self.status = ExitAssistanceStatus.REJECTED
            self._decision = "REJECT"
            self._input_timestamp = receipt_timestamp
            self._decision_commit_timestamp = commit
            self._emit(
                "lc_decision",
                commit,
                input_received=True,
                input_timestamp=receipt_timestamp,
            )
            return
        if self._automated:
            if self._lateral_timestamp is not None:
                self.status = ExitAssistanceStatus.INTERRUPTED
                self._decision = "UNAVAILABLE_MOVEMENT_STARTED"
                self._input_timestamp = receipt_timestamp
                self._decision_commit_timestamp = commit
                self._emit(
                    "lc_decision",
                    commit,
                    input_received=True,
                    input_timestamp=receipt_timestamp,
                )
                return
            observation = self._latest_observation
            if observation is None or not is_confirmation_observation(
                self.config, observation
            ):
                self.status = ExitAssistanceStatus.INTERRUPTED
                self._decision = "UNAVAILABLE_ROUTE"
                self._input_timestamp = receipt_timestamp
                self._decision_commit_timestamp = commit
                self._emit(
                    "lc_decision",
                    commit,
                    input_received=True,
                    input_timestamp=receipt_timestamp,
                )
                return
            self.status = ExitAssistanceStatus.CONFIRMED
            self._decision = "CONFIRM"
            self._input_timestamp = receipt_timestamp
            self._decision_commit_timestamp = commit
            self._emit(
                "lc_decision",
                commit,
                input_received=True,
                input_timestamp=receipt_timestamp,
            )
            if not self.config.assisted_execution_enabled:
                self.status = ExitAssistanceStatus.INTERRUPTED
                self._emit(
                    "lc_finished",
                    commit,
                    outcome="UNVERIFIED",
                    termination_reason="ASSISTED_EXECUTION_BLOCKED",
                )
                return
            if self._assisted_submission is None:
                self._reference.arm(self.config.route, observation.route_point_index)
                self._route_was_armed = True
            elif not self._assisted_submission():
                if self._submission_clock is None:
                    raise ExitPresentationError("TM submission clock is unavailable")
                self.fail_assisted_submission(self._submission_clock())

    def fail_assisted_submission(self, timestamp: TimestampEnvelope) -> None:
        """Record a rejected submission, including a deferred signal timeout."""
        if self.status is not ExitAssistanceStatus.CONFIRMED:
            return
        if self.config.signal_guided_exit:
            self._interrupt(timestamp, "ASSISTED_SUBMISSION_UNVERIFIED")
            return
        self.status = ExitAssistanceStatus.INTERRUPTED
        self._emit(
            "lc_finished", timestamp, outcome="UNVERIFIED",
            termination_reason="ASSISTED_SUBMISSION_UNVERIFIED",
        )

    def finish(
        self, reason: ExitTerminationReason, timestamp: TimestampEnvelope
    ) -> None:
        self._finished = True
        self.cancel_automatic_route(timestamp)
        self.presentation = None
        if self.status in (
            ExitAssistanceStatus.COMPLETED,
            ExitAssistanceStatus.INTERRUPTED,
            ExitAssistanceStatus.NOT_PRESENTED,
        ):
            return
        reached = self.status is not ExitAssistanceStatus.WAITING
        self.status = ExitAssistanceStatus.INTERRUPTED
        if self._route_was_armed:
            self._reference.cancel()
            self._route_was_armed = False
        self._emit(
            "lc_finished",
            timestamp,
            outcome="INTERRUPTED" if reached else "NOT_REACHED",
            termination_reason=reason.value,
        )

    def _interrupt(self, timestamp: TimestampEnvelope, reason: str) -> None:
        self.status = ExitAssistanceStatus.INTERRUPTED
        if self.config.signal_guided_exit:
            self._finished = True
            self.presentation = None
            self.cancel_automatic_route(timestamp)
        if self._route_was_armed:
            self._reference.cancel()
            self._route_was_armed = False
        self._emit(
            "lc_finished", timestamp, outcome="INTERRUPTED", termination_reason=reason
        )

    def cancel_automatic_route(self, timestamp: TimestampEnvelope) -> None:
        if not self._automatic_route_armed:
            return
        self._automatic_route_armed = False
        try:
            verified = (
                self._automatic_route_cancellation is not None
                and self._automatic_route_cancellation()
            )
        except Exception:  # noqa: BLE001 -- cancellation failure cannot stop driver control
            verified = False
        self._automatic_route_cancel_unverified = not verified
        self._emit(
            "lc_route_cancelled" if verified else "lc_route_cancel_unverified",
            timestamp,
        )

    def _emit(
        self,
        event_type: str,
        timestamp: TimestampEnvelope,
        *,
        decision: str | None = None,
        outcome: str | None = None,
        termination_reason: str | None = None,
        observed_proxy: bool = False,
        input_received: bool = False,
        observation: ExitObservation | None = None,
        input_timestamp: TimestampEnvelope | None = None,
        observation_gap_previous_frame: int | None = None,
        presentation_window_status: str | None = None,
        highway_rejoin_status: str | None = None,
    ) -> None:
        if event_type == "lc_completed" or (
            event_type == "lc_opportunity_retired"
            and termination_reason == "EXIT_CORRIDOR_LEFT"
        ):
            self._opportunity_end_timestamp = timestamp
            self._opportunity_end_outcome = outcome
            self._opportunity_end_reason = termination_reason or event_type
        event = build_exit_event(
            ExitEventData(
                config=self.config,
                event_type=event_type,
                timestamp=timestamp,
                t0=self._t0,
                frozen_mode=self._frozen_mode,
                actual_mode=self._actual_mode,
                indicator_timestamp=self._indicator_timestamp,
                steering_timestamp=self._steering_timestamp,
                lateral_timestamp=self._lateral_timestamp,
                crossing_timestamp=self._crossing_timestamp,
                decision=self._decision if decision is None else decision,
                outcome=outcome,
                termination_reason=termination_reason,
                observed_proxy=observed_proxy,
                input_received=input_received,
                observation=observation,
                input_timestamp=input_timestamp,
                decision_commit_timestamp=self._decision_commit_timestamp,
                persisted_input_timestamp=self._input_timestamp,
                observed_proxy_timestamp=self._observed_proxy_timestamp,
                observation_gap_previous_frame=observation_gap_previous_frame,
                active_drive_start=self._active_drive_start,
                eligibility_elapsed_s=self.eligibility_elapsed_seconds(timestamp),
                presentation_window_status=presentation_window_status,
                highway_rejoin_status=highway_rejoin_status,
                recommendation_at_t0=(
                    None if self._t0 is None else self._recommendation_presented
                ),
                guidance_closed_timestamp=self._guidance_closed_timestamp,
                guidance_hidden_timestamp=self._guidance_hidden_timestamp,
                route_choice=self._route_choice,
            )
        )
        self.events.append(event)
        if self._event_sink is not None:
            try:
                self._event_sink(event)
            except Exception as error:
                if not self.config.signal_guided_exit:
                    raise
                if len(self.logging_errors) < 10:
                    self.logging_errors.append(f"{type(error).__name__}:{error}")


class ExitPresentationError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
