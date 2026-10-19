"""Injected research decisions; no operational thresholds or default trigger window.

The caller supplies a predeclared policy through the existing approval contract.
An injected policy is still Offline-only in P8. It is not physical authorization.
Times are host-monotonic elapsed seconds, never CARLA/host clock differences.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.p7.types import HazardObservation, HazardOutcome, P7Error


class TriggerAction(StrEnum):
    WAIT = "WAIT"
    FIRE = "FIRE"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    action: TriggerAction
    reason: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.action, TriggerAction)
            or not isinstance(self.reason, str)
            or not self.reason.strip()
        ):
            raise P7Error("INVALID_HAZARD_TRIGGER_DECISION")


@dataclass(frozen=True, slots=True)
class OutcomeDecision:
    outcome: HazardOutcome
    reason: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.outcome, HazardOutcome)
            or self.outcome
            not in (
                HazardOutcome.AVOIDED,
                HazardOutcome.STOPPED,
                HazardOutcome.INCOMPLETE,
                HazardOutcome.UNOBSERVABLE,
            )
            or not isinstance(self.reason, str)
            or not self.reason.strip()
        ):
            # Collision/departure come from observation; a rescue request/return
            # alone must never be labeled a physically successful automatic rescue.
            raise P7Error("INVALID_HAZARD_OUTCOME_DECISION")


class HazardPolicy(Protocol):
    def trigger(
        self, observation: HazardObservation, *, elapsed_since_arm_s: float
    ) -> TriggerDecision: ...

    def outcome(
        self,
        observation: HazardObservation,
        *,
        elapsed_since_trigger_s: float,
        elapsed_since_geometric_reveal_s: float | None,
    ) -> OutcomeDecision | None: ...
