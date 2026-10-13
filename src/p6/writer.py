from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from src.p6.aoi import Aoi, AoiLayout
from src.p6.clock import AffineClockMapping, SessionOrigin
from src.p6.models import JsonScalar, RawGazeSample, Validity
from src.p6.writer_status import (
    CaptureEnvelope,
    OverflowSummary,
    UnconfirmedRange,
    WriterShutdownReport,
    persist_report,
)


class GazeSidecarWriter:
    def __init__(
        self,
        *,
        path: Path,
        run_id: str,
        origin: SessionOrigin,
        mapping: AffineClockMapping,
        layout: AoiLayout | None,
        queue_capacity: int = 2048,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file: TextIO = path.open("x", encoding="utf-8", newline="\n")
        self._queue: queue.Queue[CaptureEnvelope] = queue.Queue(queue_capacity)
        self._run_id = run_id
        self._origin = origin
        self._mapping = mapping
        self._layout = layout
        self._callback_index = 0
        self._thread: threading.Thread | None = None
        self._worker_error: OSError | TypeError | ValueError | None = None
        self._overflow: OverflowSummary | None = None
        self._capture_lock = threading.Lock()
        self._last_sequence: int | None = None
        self._last_source_timestamp: int | float | None = None
        self._stop_requested = threading.Event()
        self._accepting = True
        self._closed = False
        self._written_samples = self._overflow_total = 0
        self._unconfirmed: UnconfirmedRange | None = None
        self._overflow_range: UnconfirmedRange | None = None
        self._inflight: CaptureEnvelope | None = None
        self._report_error: str | None = None
        self._write(
            {
                "record_type": "metadata",
                "schema_version": 1,
                "run_id": run_id,
                "origin_monotonic_ns": origin.monotonic_ns,
                "origin_utc_ns": origin.utc_ns,
                "clock_mapping": mapping.provenance.as_record(),
                "aoi_version": None if layout is None else layout.version,
                "fusion_policy": "BILATERAL_RAW_PRESERVED_NO_IMPLICIT_FUSION",
            }
        )

    @property
    def worker_error(self) -> OSError | TypeError | ValueError | None:
        return self._worker_error

    @property
    def healthy(self) -> bool:
        return self._worker_error is None

    def start(self) -> None:
        with self._capture_lock:
            if not self._accepting:
                raise RuntimeError("gaze writer is closing or closed")
            if self._thread is None:
                self._launch()

    def _launch(self) -> None:
        self._thread = threading.Thread(
            target=self._drain, name="p6-gaze-writer", daemon=True
        )
        self._thread.start()

    def capture(
        self, sample: RawGazeSample, *, receive_monotonic_ns: int, receive_utc_ns: int
    ) -> None:
        with self._capture_lock:
            if not self._accepting:
                raise RuntimeError("gaze writer is closing or closed")
            envelope = CaptureEnvelope(
                self._callback_index, receive_monotonic_ns, receive_utc_ns, sample
            )
            self._callback_index += 1
            try:
                self._queue.put_nowait(envelope)
            except queue.Full:
                self._overflow_total += 1
                index, sequence, received = envelope.callback_index, sample.source_sequence, receive_monotonic_ns
                self._overflow_range = (UnconfirmedRange(1, index, index, sequence, sequence, received, received)
                                        if self._overflow_range is None else self._overflow_range.extend(index, sequence, received))
                current = self._overflow
                self._overflow = (
                    OverflowSummary(
                        1,
                        sample.source_sequence,
                        sample.source_sequence,
                        receive_monotonic_ns,
                        receive_monotonic_ns,
                    )
                    if current is None
                    else OverflowSummary(
                        current.count + 1,
                        current.first_sequence,
                        sample.source_sequence,
                        current.first_receive_monotonic_ns,
                        receive_monotonic_ns,
                    )
                )

    @property
    def shutdown_report(self) -> WriterShutdownReport:
        alive = self._thread is not None and self._thread.is_alive()
        return WriterShutdownReport(
            "CLOSING" if alive else "CLOSED_WITH_ERROR" if self._worker_error else "CLOSED",
            self._run_id, self._written_samples, self._unconfirmed, self._overflow_total, self._overflow_range,
            self._queue.qsize(), None if self._inflight is None else self._inflight.callback_index,
            None if self._inflight is None else self._inflight.sample.source_sequence,
            None if self._worker_error is None else str(self._worker_error),
            alive, self._file.closed, self._report_error,
        )

    def close(self, *, timeout: float = 5.0) -> WriterShutdownReport:
        with self._capture_lock:
            self._accepting = False
            self._stop_requested.set()
            if self._thread is None and not self._closed:
                self._launch()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise TimeoutError(f"gaze writer still owns its file: {self.shutdown_report}")
            self._thread = None
        return self.shutdown_report

    def _unwritten(self, envelope: CaptureEnvelope) -> None:
        index, sequence, received = envelope.callback_index, envelope.sample.source_sequence, envelope.receive_monotonic_ns
        self._unconfirmed = (UnconfirmedRange(1, index, index, sequence, sequence, received, received)
                             if self._unconfirmed is None else self._unconfirmed.extend(index, sequence, received))

    def _drain(self) -> None:
        envelope = None
        try:
            while not self._stop_requested.is_set() or not self._queue.empty():
                try:
                    envelope = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    self._inflight = envelope
                    self._write_overflow()
                    self._write_sample(envelope)
                    self._file.flush()
                    self._written_samples += 1
                    envelope = None
                    self._inflight = None
                finally:
                    self._queue.task_done()
            self._write_overflow()
            self._file.flush()
        except (OSError, TypeError, ValueError) as error:
            self._worker_error = error
            if envelope is not None:
                self._unwritten(envelope)
                self._inflight = None
            self._stop_requested.wait()
        finally:
            while not self._queue.empty():
                self._unwritten(self._queue.get_nowait())
                self._queue.task_done()
            try:
                self._file.close()
            except OSError as error:
                self._worker_error = self._worker_error or error
            try:
                persist_report(self.path.with_name(self.path.name + ".shutdown.json"), self.shutdown_report)
            except OSError as error:
                self._report_error = str(error)
                self._worker_error = self._worker_error or error
            self._closed = True

    def _write_sample(self, envelope: CaptureEnvelope) -> None:
        sample = envelope.sample
        duplicate = self._last_sequence == sample.source_sequence
        sequence_gap = (
            None
            if self._last_sequence is None
            else sample.source_sequence - self._last_sequence - 1
        )
        backward = (
            self._last_source_timestamp is not None
            and sample.source_timestamp < self._last_source_timestamp
        )
        self._last_sequence = sample.source_sequence
        self._last_source_timestamp = sample.source_timestamp
        valid_eyes = tuple(
            eye
            for eye in (sample.left, sample.right)
            if eye.validity is Validity.VALID
            and eye.x is not None
            and eye.y is not None
        )
        aoi = Aoi.NO_AOI
        if self._layout is not None and valid_eyes:
            x = sum(eye.x for eye in valid_eyes if eye.x is not None) / len(valid_eyes)
            y = sum(eye.y for eye in valid_eyes if eye.y is not None) / len(valid_eyes)
            aoi = self._layout.classify(x, y)
        self._write(
            {
                "record_type": "gaze",
                "run_id": self._run_id,
                "callback_index": envelope.callback_index,
                "receive_monotonic_ns": envelope.receive_monotonic_ns,
                "receive_utc_ns": envelope.receive_utc_ns,
                "receipt_session_seconds": (
                    envelope.receive_monotonic_ns - self._origin.monotonic_ns
                )
                / 1_000_000_000.0,
                "acquisition_session_seconds": self._mapping.to_session_seconds(
                    sample.source_timestamp
                )
                if sample.source_unit == self._mapping.source_unit
                else None,
                "estimated_session_seconds": self._mapping.estimate_session_seconds(sample.source_timestamp)
                if sample.source_unit == self._mapping.source_unit else None,
                "session_elapsed_s": (envelope.receive_monotonic_ns - self._origin.monotonic_ns) / 1e9,
                "session_time_basis": "HOST_RECEIPT_NOT_ACQUISITION",
                "mapping_verified": self._mapping.verified
                and sample.source_unit == self._mapping.source_unit,
                "source_sequence": sample.source_sequence,
                "source_timestamp": sample.source_timestamp,
                "source_unit": sample.source_unit,
                "clock_kind": sample.clock_kind.value,
                "left": asdict(sample.left),
                "right": asdict(sample.right),
                "source_metadata": sample.source_metadata,
                "synthetic": sample.source_metadata.get("synthetic") is True,
                "aoi": aoi.value,
                "duplicate_sequence": duplicate,
                "backward_source_time": backward,
                "sequence_gap": sequence_gap
                if sequence_gap is not None and sequence_gap > 0
                else 0,
            }
        )

    def _write_overflow(self) -> None:
        with self._capture_lock:
            overflow = self._overflow
            self._overflow = None
        if overflow is not None:
            self._write({"record_type": "overflow", **asdict(overflow)})

    def _write(self, record: dict[str, JsonScalar | dict[str, JsonScalar]]) -> None:
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
