from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.p6.models import RawGazeSample


@dataclass(frozen=True, slots=True)
class CaptureEnvelope:
    callback_index: int
    receive_monotonic_ns: int
    receive_utc_ns: int
    sample: RawGazeSample


@dataclass(frozen=True, slots=True)
class OverflowSummary:
    count: int
    first_sequence: int
    last_sequence: int
    first_receive_monotonic_ns: int
    last_receive_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class UnconfirmedRange:
    count: int
    first_callback_index: int
    last_callback_index: int
    first_sequence: int
    last_sequence: int
    first_receive_monotonic_ns: int
    last_receive_monotonic_ns: int

    def extend(self, index: int, sequence: int, received: int) -> UnconfirmedRange:
        return UnconfirmedRange(self.count + 1, self.first_callback_index, index,
                                self.first_sequence, sequence, self.first_receive_monotonic_ns, received)


@dataclass(frozen=True, slots=True)
class WriterShutdownReport:
    status: str
    run_id: str
    written_samples: int
    unconfirmed: UnconfirmedRange | None
    overflow_total: int
    overflow: UnconfirmedRange | None
    pending_samples: int
    in_flight_callback_index: int | None
    in_flight_sequence: int | None
    worker_error: str | None
    worker_alive: bool
    file_closed: bool
    report_error: str | None


def persist_report(path: Path, report: WriterShutdownReport) -> None:
    payload = asdict(report)
    payload.pop("worker_alive")
    payload["status"] = "WRITE_FAILED" if report.worker_error else "DRAINED"
    payload["range_semantics"] = "UNCONFIRMED_WRITES_MAY_CONTAIN_PARTIAL_BYTES; callback indices are receipt order"
    payload["persistence_basis"] = "PYTHON_FLUSH_NOT_POWER_LOSS_DURABILITY"
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, indent=2)
