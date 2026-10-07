from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from src.experiment.exit_assistance import ExitContext


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

    def __post_init__(self) -> None:
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
        reserved = set("npvwasdzxeh")
        keys = (self.confirm_key, self.reject_key)
        if any(len(key) != 1 or not key.isascii() or not key.isalpha() for key in keys):
            raise ResearchExitConfigError("exit keys must be single ASCII letters")
        if self.confirm_key.lower() == self.reject_key.lower() or any(
            key.lower() in reserved for key in keys
        ):
            raise ResearchExitConfigError(
                "exit keys must be distinct and non-conflicting"
            )
        object.__setattr__(self, "confirm_key", self.confirm_key.lower())
        object.__setattr__(self, "reject_key", self.reject_key.lower())


class ResearchExitConfigError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message
