from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, assert_never

from src.experiment.context import (
    ExperimentCondition,
    ExperimentModule,
    Module1Condition,
    Module2Condition,
)
from src.experiment.exit_route import ExitRoute
from src.experiment.timestamp import TimestampEnvelope


class ExitContext(StrEnum):
    TRAINING = "TRAINING"
    MODULE = "MODULE"


class ExitAssistanceStatus(StrEnum):
    WAITING = "WAITING"
    DRAFT = "DRAFT"
    PRESENTED = "PRESENTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NORESPONSE = "NORESPONSE"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    NOT_PRESENTED = "NOT_PRESENTED"


class ExitTerminationReason(StrEnum):
    NORMAL = "NORMAL"
    USER_EXIT = "USER_EXIT"
    TIME_CAP = "TIME_CAP"
    ERROR = "ERROR"


class ExitRouteReference(Protocol):
    def arm(self, route: ExitRoute, point_index: int) -> None: ...

    def cancel(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ExitAssistanceConfig:
    module: ExperimentModule
    condition: ExperimentCondition
    context: ExitContext
    lc_event_id: str
    route: ExitRoute
    navigation_trigger_distance_m: float
    response_timeout_s: float
    lateral_onset_threshold_m: float
    steering_onset_threshold: float = 0.05
    assisted_execution_enabled: bool = True
    eligibility_open_s: float | None = None
    eligibility_close_s: float | None = None
    signal_guided_exit: bool = False

    def __post_init__(self) -> None:
        match self.module, self.condition:
            case ExperimentModule.MODULE_1, (
                Module1Condition.NO_SURT | Module1Condition.SURT
            ):
                pass
            case ExperimentModule.MODULE_2, (
                Module2Condition.MANUAL | Module2Condition.NOA_L2
            ):
                pass
            case ExperimentModule.MODULE_1, (
                Module2Condition.MANUAL | Module2Condition.NOA_L2
            ) | ExperimentModule.MODULE_2, (
                Module1Condition.NO_SURT | Module1Condition.SURT
            ):
                raise ExitAssistanceConfigError("module and condition are incompatible")
            case unreachable:
                assert_never(unreachable)
        if self.route.source_lane_id != -3 or self.route.target_lane_id != -4:
            raise ExitAssistanceConfigError(
                "development envelope supports only source lane -3 to target lane -4"
            )
        values = (
            self.navigation_trigger_distance_m,
            self.response_timeout_s,
            self.lateral_onset_threshold_m,
            self.steering_onset_threshold,
        )
        if not all(isfinite(value) and value > 0.0 for value in values):
            raise ExitAssistanceConfigError(
                "exit assistance values must be finite and positive"
            )
        window = (self.eligibility_open_s, self.eligibility_close_s)
        if (window[0] is None) != (window[1] is None):
            raise ExitAssistanceConfigError(
                "eligibility window open and close must be supplied together"
            )
        if window[0] is not None and window[1] is not None and (
            not isfinite(window[0])
            or not isfinite(window[1])
            or window[0] < 0.0
            or window[1] < window[0]
        ):
            raise ExitAssistanceConfigError(
                "eligibility window must be finite, non-negative, and ordered"
            )


class ExitAssistanceConfigError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ExitPresentation:
    lc_event_id: str
    navigation_text: str
    recommendation: bool
    distance_to_exit_m: float
    observation_frame: int | None
    route_point_index: int
    signal_guided_exit: bool = False


@dataclass(frozen=True, slots=True)
class ExitObservation:
    timestamp: TimestampEnvelope
    route_distance_m: float
    lateral_offset_m: float | None
    road_id: int | None
    section_id: int | None
    lane_id: int | None
    carla_frame: int | None
    carla_simulation_seconds: float | None
    indicator: str = "off"
    lane_width_m: float = 3.5
    route_matched: bool = True
    route_point_index: int = 0
    target_boundary_valid: bool = False
    route_reference_distance_m: float | None = None
    route_reference_half_width_m: float | None = None
    right_lane_id: int | None = None


@dataclass(frozen=True, slots=True)
class ExitAssistanceEvent:
    event_type: str
    timestamp: TimestampEnvelope
    payload_json: str
