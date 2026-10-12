from __future__ import annotations

import json

from src.experiment.exit_assistance import ExitAssistanceCoordinator
from src.p8.operator_exit_ledger_view import LiveCoordinatorEvidence, LiveExitEvent


def live_evidence(
    coordinator: ExitAssistanceCoordinator | None,
) -> LiveCoordinatorEvidence | None:
    if coordinator is None:
        return None
    return LiveCoordinatorEvidence(
        coordinator.status.value,
        tuple(
            LiveExitEvent(
                event.event_type,
                event.timestamp.host.monotonic_ns,
                tuple(sorted(json.loads(event.payload_json).items())),
            )
            for event in sorted(
                coordinator.events,
                key=lambda item: item.timestamp.host.monotonic_ns,
            )
        ),
    )


def after_cleanup_evidence(
    before: LiveCoordinatorEvidence | None,
    coordinator: ExitAssistanceCoordinator | None,
) -> LiveCoordinatorEvidence | None:
    after = live_evidence(coordinator)
    if after is None:
        return before
    if before is None:
        return after
    return LiveCoordinatorEvidence(
        after.status,
        after.events,
        before.status,
        before.events,
    )
