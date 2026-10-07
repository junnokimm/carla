from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExitLedgerEvent:
    lc_event_id: str
    event_type: str
    host_monotonic_ns: int
    payload: dict[str, str | int | float | bool | None]
    row_number: int


@dataclass(frozen=True, slots=True)
class ExitLedgerSummary:
    lc_event_id: str
    events: tuple[ExitLedgerEvent, ...]
    decision: str | None
    outcome: str | None
    t0_host_monotonic_ns: int | None
    tL_host_monotonic_ns: int | None


def reconstruct_exit_ledger(path: Path) -> tuple[ExitLedgerSummary, ...]:
    grouped: dict[str, list[ExitLedgerEvent]] = {}
    with path.open(newline="", encoding="utf-8") as source:
        for row_number, row in enumerate(csv.DictReader(source), start=2):
            event_value = row.get("event_value", "")
            if not event_value.startswith("{"):
                continue
            payload = json.loads(event_value)
            event_id = payload.get("lc_event_id")
            if not isinstance(event_id, str):
                continue
            event = ExitLedgerEvent(
                event_id,
                row["event_type"],
                int(row["host_monotonic_ns"]),
                payload,
                row_number,
            )
            grouped.setdefault(event_id, []).append(event)
    summaries = []
    for event_id, values in sorted(grouped.items()):
        events = tuple(
            sorted(values, key=lambda item: (item.host_monotonic_ns, item.row_number))
        )
        decision = _latest(events, "decision")
        outcome = _latest(events, "outcome")
        summaries.append(
            ExitLedgerSummary(
                event_id,
                events,
                decision if isinstance(decision, str) else None,
                outcome if isinstance(outcome, str) else None,
                _first_int(events, "t0_host_monotonic_ns"),
                _first_int(events, "tL_host_monotonic_ns"),
            )
        )
    return tuple(summaries)


def _latest(
    events: tuple[ExitLedgerEvent, ...], key: str
) -> str | int | float | bool | None:
    for event in reversed(events):
        value = event.payload.get(key)
        if value is not None:
            return value
    return None


def _first_int(events: tuple[ExitLedgerEvent, ...], key: str) -> int | None:
    for event in events:
        value = event.payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None
