from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from src.experiment.exit_assistance import ExitContext


@dataclass(frozen=True, slots=True)
class ExitEligibilityWindow:
    open_s: float
    close_s: float

    def __post_init__(self) -> None:
        if (
            not isfinite(self.open_s)
            or not isfinite(self.close_s)
            or self.open_s < 0.0
            or self.close_s < self.open_s
        ):
            raise ResearchExitConfigError(
                "eligibility window must be finite, non-negative, and ordered"
            )


@dataclass(frozen=True, slots=True)
class ResearchExitAssistanceConfig:
    manifest_path: Path
    route_id: str
    lc_event_id: str
    context: ExitContext = ExitContext.MODULE
    navigation_trigger_distance_m: float = 1000.0
    response_timeout_s: float = 5.0
    lateral_onset_threshold_m: float = 0.15
    confirm_key: str = "c"
    reject_key: str = "r"
    assisted_execution_enabled: bool = True
    indicator_confirmation: bool = False
    eligibility_open_s: float | None = None
    eligibility_close_s: float | None = None
    signal_guided_exit: bool = False

    def __post_init__(self) -> None:
        if self.signal_guided_exit and (
            not self.indicator_confirmation
            or not self.assisted_execution_enabled
        ):
            raise ResearchExitConfigError(
                "signal-guided exit requires indicator confirmation and assisted execution"
            )
        if not self.route_id.strip() or not self.lc_event_id.strip():
            raise ResearchExitConfigError(
                "route and event identities must not be blank"
            )
        if (
            not isfinite(self.navigation_trigger_distance_m)
            or self.navigation_trigger_distance_m <= 0.0
        ):
            raise ResearchExitConfigError(
                "navigation trigger distance must be positive"
            )
        if not isfinite(self.response_timeout_s) or self.response_timeout_s <= 0.0:
            raise ResearchExitConfigError("response timeout must be positive")
        if (
            not isfinite(self.lateral_onset_threshold_m)
            or self.lateral_onset_threshold_m <= 0.0
        ):
            raise ResearchExitConfigError("lateral onset threshold must be positive")
        reserved = set("npvwasdeh" if self.indicator_confirmation else "npvwasdzxeh")
        keys = (self.confirm_key, self.reject_key)
        if any(len(key) != 1 or not key.isascii() or not key.isalpha() for key in keys):
            raise ResearchExitConfigError("exit keys must be single ASCII letters")
        if self.confirm_key.lower() == self.reject_key.lower() or any(
            key.lower() in reserved for key in keys
        ):
            raise ResearchExitConfigError(
                "exit keys must be distinct and non-conflicting"
            )
        if self.indicator_confirmation and (
            self.confirm_key.lower() != "x" or self.reject_key.lower() != "r"
        ):
            raise ResearchExitConfigError(
                "indicator confirmation requires X confirmation and R rejection"
            )
        window = (self.eligibility_open_s, self.eligibility_close_s)
        if (window[0] is None) != (window[1] is None):
            raise ResearchExitConfigError(
                "eligibility window open and close must be supplied together"
            )
        if (
            window[0] is not None
            and window[1] is not None
            and (
                not isfinite(window[0])
                or not isfinite(window[1])
                or window[0] < 0.0
                or window[1] < window[0]
            )
        ):
            raise ResearchExitConfigError(
                "eligibility window must be finite, non-negative, and ordered"
            )
        object.__setattr__(self, "confirm_key", self.confirm_key.lower())
        object.__setattr__(self, "reject_key", self.reject_key.lower())


class ResearchExitConfigError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message
