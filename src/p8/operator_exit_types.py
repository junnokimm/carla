from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.p7.types import Json


class ExitOpportunityClassification(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED_NOT_RUN = "CONFIGURED_NOT_RUN"
    CONFIGURED_NOT_PRESENTED = "CONFIGURED_NOT_PRESENTED"
    PRESENTED = "PRESENTED"
    DECISION_RECORDED = "DECISION_RECORDED"
    NORMAL_MISSED = "NORMAL_MISSED"
    EXIT_CONFIRMED = "EXIT_CONFIRMED"
    PARTICIPANT_INTERRUPTED = "PARTICIPANT_INTERRUPTED"
    BLOCKED = "BLOCKED"
    INCOMPLETE = "INCOMPLETE"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"


@dataclass(frozen=True, slots=True)
class ExitOpportunityResult:
    classification: ExitOpportunityClassification
    configured: bool
    lc_event_id: str | None = None
    context: str | None = None
    manifest_path: str | None = None
    route_id: str | None = None
    route_version: str | None = None
    coordinator_status: str | None = None
    decision: str | None = None
    physical_outcome: str | None = None
    termination_reason: str | None = None
    noa_state_t0: str | None = None
    t0_host_monotonic_ns: int | None = None
    t_l_host_monotonic_ns: int | None = None
    terminal_event: str | None = None
    technical_error: str | None = None
    secondary_errors: tuple[str, ...] = ()

    @property
    def observed_opportunity(self) -> bool:
        return self.physical_outcome in ("EXIT", "MISSED") and self.classification in (
            ExitOpportunityClassification.NORMAL_MISSED,
            ExitOpportunityClassification.EXIT_CONFIRMED,
            ExitOpportunityClassification.BLOCKED,
        )

    def manifest(self) -> dict[str, Json]:
        return {
            "classification": self.classification.value,
            "configured": self.configured,
            "lc_event_id": self.lc_event_id,
            "context": self.context,
            "manifest_path": self.manifest_path,
            "route_id": self.route_id,
            "route_version": self.route_version,
            "coordinator_status": self.coordinator_status,
            "decision": self.decision,
            "physical_outcome": self.physical_outcome,
            "termination_reason": self.termination_reason,
            "noa_state_t0": self.noa_state_t0,
            "t0_host_monotonic_ns": self.t0_host_monotonic_ns,
            "tL_host_monotonic_ns": self.t_l_host_monotonic_ns,
            "terminal_event": self.terminal_event,
            "technical_error": self.technical_error,
            "secondary_errors": list(self.secondary_errors),
            "observed_opportunity": self.observed_opportunity,
        }
