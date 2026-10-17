from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from src.experiment.timestamp import TimestampEnvelope
from src.p7.config import HazardConfig
from src.p7.hazard import HazardActors
from src.p7.policy import HazardPolicy
from src.p7.types import HazardObservation, P7Error

if TYPE_CHECKING:
    from src.p8.operator_continuity import SharedRuntimeIdentities
    from src.p8.services import SessionServices
    from src.scenario.research_noa_persistence import ResearchSnapshotSample
    from src.scenario.research_noa_session import ResearchNoASession


@dataclass(frozen=True, slots=True)
class HazardPolicyApproval:
    config: HazardConfig
    policy_source: str
    approval_reference: str
    backend_readiness_source: str
    policy: HazardPolicy | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.policy_source,
                self.approval_reference,
                self.backend_readiness_source,
            )
        ):
            raise P7Error("HAZARD_APPROVAL_PROVENANCE_REQUIRED")


@dataclass(frozen=True, slots=True)
class HazardCleanupEvidence:
    failures: tuple[str, ...]
    remaining_actor_ids: tuple[int, ...]


class OperatorHazardBackend(Protocol):
    @property
    def actors(self) -> HazardActors: ...

    def observe_geometry(
        self, sample: ResearchSnapshotSample
    ) -> HazardObservation | None:
        """Use this exact cached observation; preserve its timestamp/provenance.

        Returning another observation and relabeling it as fresh is prohibited.
        Independent sensor events retain their own frame/timestamp fields.
        """
        ...

    def cleanup(self) -> HazardCleanupEvidence: ...


class HazardSafetyOwner(Protocol):
    def request_safe_shutdown(self, timestamp: TimestampEnvelope) -> None: ...


@dataclass(frozen=True, slots=True)
class HazardExecutionResources:
    session: ResearchNoASession
    services: SessionServices
    identities: SharedRuntimeIdentities


class HazardBackendFactory(Protocol):
    def acquire(self, resources: HazardExecutionResources) -> OperatorHazardBackend: ...

    def cleanup_partial(self) -> HazardCleanupEvidence: ...


@dataclass(frozen=True, slots=True)
class HazardExecutionPlan:
    approval: HazardPolicyApproval
    backend_factory: HazardBackendFactory
    safety_owner: HazardSafetyOwner
