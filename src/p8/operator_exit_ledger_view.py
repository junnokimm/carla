from __future__ import annotations

from dataclasses import dataclass

from src.scenario.research_exit_ledger import ExitLedgerEvent

type ExitPayloadValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class LiveExitEvent:
    event_type: str
    host_monotonic_ns: int
    payload: tuple[tuple[str, ExitPayloadValue], ...]


@dataclass(frozen=True, slots=True)
class LiveCoordinatorEvidence:
    status: str
    events: tuple[LiveExitEvent, ...]
    pre_cleanup_status: str | None = None
    pre_cleanup_events: tuple[LiveExitEvent, ...] = ()


def immutable_event(event: ExitLedgerEvent) -> LiveExitEvent:
    return LiveExitEvent(
        event.event_type,
        event.host_monotonic_ns,
        tuple(sorted(event.payload.items())),
    )


def events_of_type(
    events: tuple[ExitLedgerEvent, ...], event_type: str
) -> tuple[ExitLedgerEvent, ...]:
    return tuple(event for event in events if event.event_type == event_type)


def latest_text(events: tuple[ExitLedgerEvent, ...], key: str) -> str | None:
    for event in reversed(events):
        value = event.payload.get(key)
        if isinstance(value, str):
            return value
    return None
