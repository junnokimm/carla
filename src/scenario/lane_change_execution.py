from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LaneChangeSubmissionStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    UNVERIFIED = "UNVERIFIED"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True, slots=True)
class LaneChangeSubmission:
    request_id: str
    generation: int
    direction: str
    status: LaneChangeSubmissionStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class StaleLaneChangeGenerationError(RuntimeError):
    expected: int
    received: int

    def __str__(self) -> str:
        return (
            "stale lane-change activation generation: "
            f"expected {self.expected}, received {self.received}"
        )
