from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

TARGETS = ("FORWARD", "REAR_MIRROR", "LEFT_MIRROR", "RIGHT_MIRROR", "MISSING")
type Phase = Literal["PREPARE", "MEASURING", "DONE", "ABORTED"]


@dataclass(frozen=True, slots=True)
class SegmentWindow:
    index: int
    target: str
    start_ns: int
    end_ns: int
    complete: bool = False


@dataclass(frozen=True, slots=True)
class SegmentEvent:
    validation_run_id: str
    event: str
    segment_index: int
    expected_target: str
    host_monotonic_ns: int
    session_elapsed_s: float
    reason: str | None = None


class SegmentSequence:
    def __init__(self, run_id: str, origin_ns: int,
                 sink: Callable[[SegmentEvent], None] | None = None) -> None:
        self.run_id, self.origin_ns = run_id, origin_ns
        self.phase: Phase = "PREPARE"
        self.index = 0
        self.windows: list[SegmentWindow] = []
        self.events: list[SegmentEvent] = []
        self._sink = sink

    @property
    def target(self) -> str:
        return TARGETS[self.index]

    def prepare(self, now_ns: int) -> None:
        self._emit("PREPARE", now_ns)

    def start(self, now_ns: int) -> None:
        if self.phase != "PREPARE":
            return
        self.windows.append(SegmentWindow(self.index, self.target, now_ns, now_ns + 2_000_000_000))
        self.phase = "MEASURING"
        self._emit("START", now_ns)

    def advance(self, now_ns: int) -> None:
        if self.phase != "MEASURING" or now_ns < self.windows[-1].end_ns:
            return
        self.windows[-1] = replace(self.windows[-1], complete=True)
        self._emit("END", self.windows[-1].end_ns)
        if self.index == len(TARGETS) - 1:
            self.phase = "DONE"
        else:
            self.index += 1
            self.phase = "PREPARE"
            self.prepare(now_ns)

    def abort(self, now_ns: int, reason: str) -> None:
        if self.phase in ("ABORTED", "DONE"):
            return
        if self.phase == "MEASURING":
            self.windows[-1] = replace(self.windows[-1], end_ns=min(now_ns, self.windows[-1].end_ns))
        self.phase = "ABORTED"
        self._emit("ABORT", now_ns, reason)

    def segment_at(self, sample_ns: int) -> SegmentWindow | None:
        return next((window for window in self.windows
                     if window.start_ns <= sample_ns < window.end_ns), None)

    def _emit(self, event: str, now_ns: int, reason: str | None = None) -> None:
        record = SegmentEvent(self.run_id, event, self.index, self.target, now_ns,
                              (now_ns - self.origin_ns) / 1e9, reason)
        self.events.append(record)
        if self._sink is not None:
            self._sink(record)
