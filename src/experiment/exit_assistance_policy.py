from __future__ import annotations

from math import isfinite
from typing import assert_never

from src.experiment.automation import DrivingControlMode
from src.experiment.context import ExperimentModule, Module2Condition
from src.experiment.exit_assistance_types import (
    ExitAssistanceConfig,
    ExitAssistanceStatus,
    ExitObservation,
    ExitPresentation,
)
from src.experiment.timestamp import TimestampEnvelope


def navigation_distance_m(
    config: ExitAssistanceConfig, observation: ExitObservation
) -> float | None:
    station = observation.route_distance_m
    if (
        not observation.route_matched
        or not isfinite(station)
        or not 0.0 <= station <= config.route.fork_distance_m
    ):
        return None
    return config.route.fork_distance_m - station


def is_navigation_observation(
    config: ExitAssistanceConfig, observation: ExitObservation
) -> bool:
    route = config.route
    return (
        navigation_distance_m(config, observation) is not None
        and (
            guidance_approach_lane(config, observation)
            if config.signal_guided_exit
            else observation.lane_id == route.source_lane_id
        )
        and route.navigation_start
        <= observation.route_distance_m
        <= route.navigation_end
    )


def guidance_approach_lane(config: ExitAssistanceConfig, observation: ExitObservation) -> bool:
    """Only the same-direction, same-road approach lanes of this route."""
    route = config.route
    index = observation.route_point_index
    if not 0 <= index < len(route.points) or observation.lane_id is None:
        return False
    point = route.points[index]
    return (
        (observation.road_id, observation.section_id) == (point.road_id, point.section_id)
        and route.target_lane_id <= observation.lane_id < 0
    )


def is_confirmation_observation(
    config: ExitAssistanceConfig, observation: ExitObservation
) -> bool:
    route = config.route
    return (
        is_navigation_observation(config, observation)
        and route.confirmation_start
        <= observation.route_distance_m
        <= route.confirmation_end
    )


def is_automated_branch(config: ExitAssistanceConfig, mode: DrivingControlMode) -> bool:
    if config.signal_guided_exit:
        return mode is DrivingControlMode.NOA_ACTIVE
    match config.module:
        case ExperimentModule.MODULE_1:
            return mode is DrivingControlMode.NOA_ACTIVE
        case ExperimentModule.MODULE_2:
            return (
                config.condition is Module2Condition.NOA_L2
                and mode is DrivingControlMode.NOA_ACTIVE
            )
        case unreachable:
            assert_never(unreachable)


def has_timed_out(
    t0: TimestampEnvelope | None,
    timestamp: TimestampEnvelope,
    timeout_s: float,
) -> bool:
    if t0 is None:
        return False
    return (
        timestamp.host.monotonic_ns - t0.host.monotonic_ns >= timeout_s * 1_000_000_000
    )


def draft_presentation(
    config: ExitAssistanceConfig,
    remaining_m: float,
    recommendation: bool,
    observation_frame: int | None,
    route_point_index: int,
) -> ExitPresentation:
    distance_m = max(remaining_m, 0.0)
    return ExitPresentation(
        config.lc_event_id,
        (
            f"전방 {distance_m / 1000.0:.1f} km, 오른쪽 출구입니다"
            if config.signal_guided_exit
            else f"{distance_m / 1000.0:.1f} km 앞 출구, 우측 차로로"
        ),
        recommendation,
        distance_m,
        observation_frame,
        route_point_index,
        signal_guided_exit=config.signal_guided_exit,
    )


def lane_change_is_in_progress(
    status: ExitAssistanceStatus,
    maneuver_in_progress: bool,
) -> bool:
    return status is ExitAssistanceStatus.CONFIRMED or (
        maneuver_in_progress
        and status
        in (
            ExitAssistanceStatus.PRESENTED,
            ExitAssistanceStatus.REJECTED,
            ExitAssistanceStatus.NORESPONSE,
        )
    )
