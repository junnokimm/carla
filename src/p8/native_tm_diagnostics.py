from __future__ import annotations

import sys
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, Protocol, TextIO

from src.error_notes import append_error_note
from src.p8.native_tm_diagnostics_jsonl import NativeTrafficManagerDiagnosticsJsonl
from src.p8.native_tm_diagnostics_schema import (
    NativeTrafficManagerDiagnosticsSchemaError,
    validate_snapshot,
)
from src.p8.native_tm_diagnostics_types import (
    JsonValue,
    NativeTrafficManagerDiagnosticsConfig,
    NativeTrafficManagerDiagnosticsContext,
    NativeTrafficManagerDiagnosticsStatus,
    native_tm_diagnostic_path,
)
from src.p8.tm_runtime_attestation import RuntimeAttestation
from src.vehicle import VehicleObservation

_DIAGNOSTIC_FAILURES: Final = (
    KeyboardInterrupt,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class DiagnosticTrafficManager(Protocol):
    def enable_diagnostic_trace(self, actor_id: int, capacity: int) -> None: ...
    def read_diagnostic_trace(self) -> Mapping[str, JsonValue]: ...
    def disable_diagnostic_trace(self) -> None: ...


class NativeTrafficManagerDiagnosticsCollector:
    def __init__(
        self,
        manager: DiagnosticTrafficManager,
        path: Path,
        stream: TextIO,
        actor_id: int,
        config: NativeTrafficManagerDiagnosticsConfig,
        context: NativeTrafficManagerDiagnosticsContext,
        monotonic_clock: Callable[[], float],
    ) -> None:
        self._manager, self.path, self._stream = manager, path, stream
        self._actor_id, self._config, self._context = actor_id, config, context
        self._clock = monotonic_clock
        self._jsonl = NativeTrafficManagerDiagnosticsJsonl(stream, context, actor_id)
        self._next_poll = monotonic_clock() + config.interval_seconds
        self._seen_order: deque[tuple[int, int]] = deque()
        self._seen: set[tuple[int, int]] = set()
        self._loss = {"overwritten": 0, "contention_dropped": 0, "internal_dropped": 0}
        self._trace_session: int | None = None
        self._records_written = 0
        self._receipt_id = 0
        self._first_error: BaseException | None = None
        self._closed = False

    @classmethod
    def open(
        cls,
        manager: DiagnosticTrafficManager,
        *,
        actor_id: int,
        output_dir: Path,
        attestation: RuntimeAttestation,
        config: NativeTrafficManagerDiagnosticsConfig,
        context: NativeTrafficManagerDiagnosticsContext,
        monotonic_clock: Callable[[], float],
        stream_factory: Callable[[Path], TextIO] | None = None,
    ) -> NativeTrafficManagerDiagnosticsCollector:
        path = native_tm_diagnostic_path(
            output_dir, context.originating_stage, actor_id
        )
        stream = (
            path.open("x", encoding="utf-8", newline="\n")
            if stream_factory is None
            else stream_factory(path)
        )
        try:
            collector = cls(
                manager, path, stream, actor_id, config, context, monotonic_clock
            )
        except _DIAGNOSTIC_FAILURES as error:
            try:
                stream.close()
            except _DIAGNOSTIC_FAILURES as close_error:
                append_error_note(
                    error,
                    f"native TM diagnostic construction stream close failure: {close_error}",
                )
            raise
        try:
            collector._jsonl.write(
                collector._jsonl.common("header")
                | {
                    "schema_version": 2,
                    "capacity": config.capacity,
                    "interval_seconds": config.interval_seconds,
                    "traffic_manager_port": attestation.port,
                    "owner_pid": attestation.owner_pid,
                    "libcarla_path": str(attestation.libcarla_path),
                    "libcarla_sha256": attestation.libcarla_sha256,
                }
            )
        except _DIAGNOSTIC_FAILURES as error:
            try:
                stream.close()
            except _DIAGNOSTIC_FAILURES as close_error:
                append_error_note(
                    error,
                    f"native TM diagnostic header stream close failure: {close_error}",
                )
            raise
        try:
            manager.enable_diagnostic_trace(actor_id, config.capacity)
            collector._capture("initial", None)
        except _DIAGNOSTIC_FAILURES as error:
            collector._latch(error)
            collector._write_failure_safely("startup", error)
            collector._attempt("startup disable", manager.disable_diagnostic_trace)
            collector._attempt("startup stream close", stream.close)
            raise collector._first_error or error
        return collector

    def poll(self, observation: VehicleObservation) -> None:
        try:
            now = self._clock()
            if now < self._next_poll:
                return
            self._capture("periodic", observation)
        except _DIAGNOSTIC_FAILURES as error:
            self._latch(error)
            self._write_failure_safely("periodic", error)
            raise self._first_error or error
        self._next_poll = now + self._config.interval_seconds

    @property
    def status(self) -> NativeTrafficManagerDiagnosticsStatus:
        coverage = self._coverage_status()
        return NativeTrafficManagerDiagnosticsStatus(
            str(self.path),
            self._actor_id,
            self._context.originating_stage,
            self._context.measurement_phase(),
            self._trace_session,
            coverage,
            coverage == "COMPLETE",
            self._records_written,
            self._loss["overwritten"],
            self._loss["contention_dropped"],
            self._loss["internal_dropped"],
            None
            if self._first_error is None
            else f"{type(self._first_error).__name__}:{self._first_error}",
            tuple(getattr(self._first_error, "__notes__", ())),
        )

    def close(self) -> None:
        if self._closed:
            return
        inherited_error = sys.exception()
        if isinstance(inherited_error, KeyboardInterrupt):
            self.mark_interrupted(inherited_error)
        self._capture_safely("final_enabled", None)
        self._attempt("disable", self._manager.disable_diagnostic_trace)
        self._capture_safely("final_disabled", None)
        self._attempt(
            "summary",
            lambda: self._jsonl.write(
                self._jsonl.common("final_summary")
                | self.status.payload()
                | {"artifact_durability": "PENDING_STREAM_CLOSE"}
            ),
        )
        self._attempt("stream close", self._stream.close)
        self._closed = True
        if self._first_error is not None:
            raise self._first_error

    def _capture_safely(
        self, phase: str, observation: VehicleObservation | None
    ) -> None:
        try:
            self._capture(phase, observation)
        except _DIAGNOSTIC_FAILURES as error:
            self._latch(error)
            self._write_failure_safely(phase, error)

    def _capture(self, phase: str, observation: VehicleObservation | None) -> None:
        begin_mono, begin_utc = (
            self._context.host_monotonic_ns(),
            self._context.host_utc_ns(),
        )
        raw = self._manager.read_diagnostic_trace()
        end_mono, end_utc = (
            self._context.host_monotonic_ns(),
            self._context.host_utc_ns(),
        )
        snapshot = validate_snapshot(
            raw,
            phase,
            self._actor_id,
            self._config.capacity,
            self._trace_session,
            self._loss,
        )
        self._loss.update(snapshot.loss)
        self._receipt_id += 1
        timing = self._jsonl.receipt_timing(
            begin_mono, begin_utc, end_mono, end_utc, observation
        )
        self._jsonl.write(
            self._jsonl.common("snapshot_receipt", end_mono, end_utc)
            | timing
            | snapshot.receipt(self._receipt_id, phase)
        )
        self._trace_session = snapshot.trace_session
        identities: set[tuple[int, int]] = set()
        for record in snapshot.records:
            identity = (snapshot.trace_session, record.event_sequence)
            if identity in identities:
                raise NativeTrafficManagerDiagnosticsSchemaError(
                    "event_sequence", "must be unique within a snapshot"
                )
            identities.add(identity)
            if identity in self._seen:
                continue
            self._jsonl.write(
                self._jsonl.common("native_record", end_mono, end_utc)
                | timing
                | {
                    "receipt_id": self._receipt_id,
                    "native_payload": record.payload,
                }
            )
            self._remember(identity)
            self._records_written += 1

    def _remember(self, identity: tuple[int, int]) -> None:
        if len(self._seen_order) >= self._config.capacity:
            self._seen.remove(self._seen_order.popleft())
        self._seen_order.append(identity)
        self._seen.add(identity)

    def _coverage_status(self) -> str:
        if self._first_error is not None:
            return "FAILED_OBSERVER"
        if self._loss["contention_dropped"] or self._loss["internal_dropped"]:
            return "INCOMPLETE_NATIVE_DROP"
        if self._loss["overwritten"]:
            return "UNKNOWN_OVERWRITE"
        return "COMPLETE"

    def _attempt(self, phase: str, action: Callable[[], None]) -> None:
        try:
            action()
        except _DIAGNOSTIC_FAILURES as error:
            self._latch(error, phase)

    def mark_interrupted(self, error: KeyboardInterrupt) -> None:
        self._latch(error, "operator interruption")

    def _write_failure_safely(self, phase: str, error: BaseException) -> None:
        self._attempt(
            "failure row",
            lambda: self._jsonl.write(
                self._jsonl.common("observer_failure")
                | {
                    "failure_phase": phase,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ),
        )

    def _latch(self, error: BaseException, phase: str | None = None) -> None:
        if self._first_error is None:
            self._first_error = error
        elif error is not self._first_error:
            append_error_note(
                self._first_error,
                f"native TM diagnostic {phase or 'observer'} failure: {type(error).__name__}:{error}",
            )
