from __future__ import annotations

from collections.abc import Callable

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance_event import ExitEventData, build_exit_event
from src.experiment.exit_assistance_observation import observe_exit
from src.experiment.exit_assistance_policy import (
    draft_presentation,
    has_timed_out,
    is_automated_branch,
    is_confirmation_observation,
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
    ) -> None:
        self.config = config
        self._reference = reference
        self.status = ExitAssistanceStatus.WAITING
        self.presentation: ExitPresentation | None = None
        self.events: list[ExitAssistanceEvent] = []
        self._event_sink = event_sink
        self._t0: TimestampEnvelope | None = None
        self._frozen_mode: DrivingControlMode | None = None
        self._actual_mode: DrivingControlMode | None = None
        self._automated = False
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
        self._route_was_armed = False

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
            return self.presentation
        remaining = self.config.route.fork_distance_m - observation.route_distance_m
        self.presentation = draft_presentation(
            self.config,
            remaining,
            is_automated_branch(self.config, state.control_mode),
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
        observation = self._latest_observation
        if (
            self.status is not ExitAssistanceStatus.DRAFT
            or observation is None
            or rendered != self.presentation
            or rendered.observation_frame != observation.carla_frame
            or rendered.route_point_index != observation.route_point_index
        ):
            raise ExitPresentationError("rendered exit presentation is stale")
        if not is_confirmation_observation(self.config, observation):
            raise ExitPresentationError("vehicle is outside exit presentation envelope")
        self.status = ExitAssistanceStatus.PRESENTED
        self._t0 = timestamp
        self._frozen_mode = state.control_mode
        self._actual_mode = state.control_mode
        self._automated = is_automated_branch(self.config, state.control_mode)
        self._baseline_lateral_m = observation.lateral_offset_m
        self._baseline_indicator = observation.indicator
        self._previous_indicator = observation.indicator
        self._previous_lateral_m = observation.lateral_offset_m
        self.presentation = rendered
        self._emit("lc_presented", timestamp)

    def update(
        self,
        driver_input: DriverInput,
        state: AutomationState,
        receipt_timestamp: TimestampEnvelope,
        commit_timestamp: TimestampEnvelope | None = None,
    ) -> None:
        commit = receipt_timestamp if commit_timestamp is None else commit_timestamp
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
        if self.status not in (
            ExitAssistanceStatus.PRESENTED,
            ExitAssistanceStatus.CONFIRMED,
        ):
            return
        if (
            self.status is ExitAssistanceStatus.PRESENTED
            and self._automated
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
            self._reference.arm(self.config.route, observation.route_point_index)
            self._route_was_armed = True
            self._emit(
                "lc_decision",
                commit,
                input_received=True,
                input_timestamp=receipt_timestamp,
            )

    def finish(
        self, reason: ExitTerminationReason, timestamp: TimestampEnvelope
    ) -> None:
        if self.status in (
            ExitAssistanceStatus.COMPLETED,
            ExitAssistanceStatus.INTERRUPTED,
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
        if self._route_was_armed:
            self._reference.cancel()
            self._route_was_armed = False
        self._emit(
            "lc_finished", timestamp, outcome="INTERRUPTED", termination_reason=reason
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
    ) -> None:
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
            )
        )
        self.events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)


class ExitPresentationError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
