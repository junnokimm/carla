from __future__ import annotations

import threading
import time
from collections.abc import Callable

from src.p6.clock import SessionOrigin
from src.p6.tablet_models import EventRequest, TabletEvent

type EventSink = Callable[[TabletEvent], None]


class TabletEventLedger:
    def __init__(
        self,
        origin: SessionOrigin,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sink: EventSink | None = None,
    ) -> None:
        self._origin = origin
        self._monotonic_ns = monotonic_ns
        self._events: list[TabletEvent] = []
        self._seen_ids: set[str] = set()
        self._sink = sink
        self._lock = threading.RLock()

    def contains(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._seen_ids

    def append(self, request: EventRequest) -> TabletEvent:
        with self._lock:
            disposition = (
                "duplicate"
                if request.event_id in self._seen_ids
                else request.disposition
            )
            self._seen_ids.add(request.event_id)
            received = request.received_monotonic_ns or self._monotonic_ns()
            event = TabletEvent(
                event_id=request.event_id,
                event_type=request.event_type,
                run_epoch=request.run_epoch,
                trial_uuid=request.trial_uuid,
                receive_monotonic_ns=received,
                receipt_session_seconds=(received - self._origin.monotonic_ns)
                / 1_000_000_000.0,
                browser_performance_ms=request.performance_ms,
                browser_time_origin_ms=request.time_origin_ms,
                browser_mapping_seconds=None,
                mapping_status="RECEIPT_ONLY_UNVERIFIED",
                disposition=disposition,
                detail=request.detail,
                correct=request.correct,
            )
            self._events.append(event)
        if self._sink is not None:
            self._sink(event)
        return event

    def sync(self, request: EventRequest) -> TabletEvent:
        return self.append(request)

    def events(self) -> tuple[TabletEvent, ...]:
        with self._lock:
            return tuple(self._events)
