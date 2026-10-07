from __future__ import annotations

from typing import Protocol

from src.experiment.exit_assistance_policy import (
    draft_presentation,
    is_confirmation_observation,
    is_navigation_observation,
)
from src.experiment.exit_assistance_types import (
    ExitAssistanceConfig,
    ExitAssistanceStatus,
    ExitObservation,
    ExitPresentation,
    ExitRouteReference,
)
from src.experiment.timestamp import TimestampEnvelope


class ObservationLifecycle(Protocol):
    config: ExitAssistanceConfig
    status: ExitAssistanceStatus
    presentation: ExitPresentation | None
    _reference: ExitRouteReference
    _automated: bool
    _decision: str | None
    _indicator_timestamp: TimestampEnvelope | None
    _lateral_timestamp: TimestampEnvelope | None
    _crossing_timestamp: TimestampEnvelope | None
    _last_observation_frame: int | None
    _latest_observation: ExitObservation | None
    _baseline_lateral_m: float | None
    _baseline_indicator: str
    _previous_indicator: str
    _previous_lateral_m: float | None
    _maneuver_in_progress: bool
    _path_outcome: str | None
    _route_was_armed: bool
    _observed_proxy_timestamp: TimestampEnvelope | None

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
    ) -> None: ...


def observe_exit(subject: ObservationLifecycle, observation: ExitObservation) -> None:
    frame = observation.carla_frame
    previous_frame = subject._last_observation_frame
    if frame is not None and previous_frame is not None and frame <= previous_frame:
        subject._emit(
            "lc_observation_gap",
            observation.timestamp,
            observation=observation,
            observation_gap_previous_frame=previous_frame,
        )
        return
    if frame is not None and previous_frame is not None and frame > previous_frame + 1:
        subject._emit(
            "lc_observation_gap",
            observation.timestamp,
            observation=observation,
            observation_gap_previous_frame=previous_frame,
        )
    subject._last_observation_frame = frame
    subject._latest_observation = observation
    remaining = subject.config.route.fork_distance_m - observation.route_distance_m
    if (
        subject.status is ExitAssistanceStatus.WAITING
        and is_navigation_observation(subject.config, observation)
        and is_confirmation_observation(subject.config, observation)
        and remaining >= 0.0
        and remaining <= subject.config.navigation_trigger_distance_m
    ):
        subject.status = ExitAssistanceStatus.DRAFT
        subject.presentation = draft_presentation(
            subject.config,
            remaining,
            False,
            frame,
            observation.route_point_index,
        )
        subject._emit("lc_draft", observation.timestamp, observation=observation)
    elif (
        subject.status is ExitAssistanceStatus.DRAFT
        and is_navigation_observation(subject.config, observation)
        and is_confirmation_observation(subject.config, observation)
    ):
        subject.presentation = draft_presentation(
            subject.config,
            remaining,
            False,
            frame,
            observation.route_point_index,
        )
    elif subject.status is ExitAssistanceStatus.DRAFT:
        subject.status = ExitAssistanceStatus.WAITING
        subject.presentation = None
    elif (
        subject.presentation is not None
        and subject.status is not ExitAssistanceStatus.COMPLETED
    ):
        subject.presentation = draft_presentation(
            subject.config,
            remaining,
            subject.status is ExitAssistanceStatus.PRESENTED and subject._automated,
            frame,
            observation.route_point_index,
        )

    if subject._baseline_lateral_m is None:
        if (
            not observation.target_boundary_valid
            or observation.lateral_offset_m is None
        ):
            return
        subject._baseline_lateral_m = observation.lateral_offset_m
        subject._previous_lateral_m = observation.lateral_offset_m
    if observation.lateral_offset_m is None:
        return

    lateral = observation.lateral_offset_m
    movement = lateral - subject._baseline_lateral_m
    if (
        subject._lateral_timestamp is None
        and movement >= subject.config.lateral_onset_threshold_m
        and observation.target_boundary_valid
    ):
        subject._lateral_timestamp = observation.timestamp
        subject._maneuver_in_progress = True
        if not subject._automated:
            subject._decision = "MANUAL_CHANGE_OBSERVED_PROXY"
            subject._observed_proxy_timestamp = observation.timestamp
        subject._emit(
            "lc_maneuver_start",
            observation.timestamp,
            observed_proxy=not subject._automated,
            observation=observation,
        )

    if (
        subject._indicator_timestamp is None
        and subject.status
        not in (ExitAssistanceStatus.WAITING, ExitAssistanceStatus.DRAFT)
        and subject._previous_indicator != "right"
        and observation.indicator == "right"
    ):
        subject._indicator_timestamp = observation.timestamp
        subject._emit(
            "lc_indicator_onset", observation.timestamp, observation=observation
        )
    subject._previous_indicator = observation.indicator

    target_boundary = -observation.lane_width_m / 2.0
    if (
        subject._crossing_timestamp is None
        and observation.target_boundary_valid
        and subject._previous_lateral_m is not None
        and subject._previous_lateral_m < target_boundary <= lateral
    ):
        subject._crossing_timestamp = observation.timestamp
        subject._emit(
            "lc_boundary_crossing", observation.timestamp, observation=observation
        )

    identity = (observation.road_id, observation.section_id, observation.lane_id)
    route = subject.config.route
    separated_through = (
        identity in route.through_segments
        and observation.route_reference_distance_m is not None
        and observation.route_reference_half_width_m is not None
        and observation.route_reference_distance_m > observation.route_reference_half_width_m
    )
    if identity in route.exit_segments:
        subject._path_outcome = "EXIT"
    elif separated_through:
        subject._path_outcome = "MISSED"

    if subject._maneuver_in_progress and (
        observation.lane_id == route.target_lane_id
        or identity in route.exit_segments
        or identity in route.through_segments
    ):
        subject._maneuver_in_progress = False
    subject._previous_lateral_m = lateral

    completion = (
        route.points[-1].distance_m
        if route.completion_distance_m is None
        else route.completion_distance_m
    )
    terminal_exit = bool(route.exit_segments) and identity == route.exit_segments[-1]
    terminal_missed = separated_through
    station_complete = (
        observation.route_matched and observation.route_distance_m >= completion
    )
    if subject._path_outcome is None or not (
        terminal_exit or terminal_missed or station_complete
    ):
        return
    if subject.status is ExitAssistanceStatus.COMPLETED:
        return
    if (
        not subject._automated
        and subject._path_outcome == "MISSED"
        and subject._lateral_timestamp is None
        and subject._decision is None
    ):
        subject._decision = "MANUAL_NOCHANGE"
    subject.status = ExitAssistanceStatus.COMPLETED
    subject.presentation = None
    subject._emit(
        "lc_completed",
        observation.timestamp,
        outcome=subject._path_outcome,
        observation=observation,
    )
    if subject._route_was_armed:
        subject._reference.cancel()
        subject._route_was_armed = False
