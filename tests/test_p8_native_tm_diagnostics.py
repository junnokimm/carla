from __future__ import annotations

import io
import json
from pathlib import Path

import carla
import pytest

from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.p8.native_tm_diagnostics import (
    JsonValue,
    NativeTrafficManagerDiagnosticsCollector,
    NativeTrafficManagerDiagnosticsConfig,
    NativeTrafficManagerDiagnosticsContext,
    NativeTrafficManagerDiagnosticsSchemaError,
)
from src.p8.native_tm_diagnostics_types import (
    NativeTrafficManagerDiagnosticsConfigError,
)
from src.p8.tm_runtime_attestation import RuntimeAttestation
from src.vehicle import VehicleObservation, VehicleState


class FakeDiagnosticTrafficManager:
    def __init__(self, snapshots: list[dict[str, JsonValue]]) -> None:
        self.snapshots = snapshots
        self.calls: list[tuple[str, int, int] | tuple[str]] = []
        self.enable_error: RuntimeError | None = None

    def enable_diagnostic_trace(self, actor_id: int, capacity: int) -> None:
        self.calls.append(("enable", actor_id, capacity))
        if self.enable_error is not None:
            raise self.enable_error

    def read_diagnostic_trace(self) -> dict[str, JsonValue]:
        self.calls.append(("read",))
        return self.snapshots.pop(0)

    def disable_diagnostic_trace(self) -> None:
        self.calls.append(("disable",))


class IncrementingNanoseconds:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 10
        return self.value


def _snapshot(
    records: list[dict[str, JsonValue]],
    *,
    enabled: bool = True,
    overwritten: int = 0,
    contention_dropped: int = 0,
    internal_dropped: int = 0,
) -> dict[str, JsonValue]:
    return {
        "selected_actor": 42,
        "enabled": enabled,
        "capacity": 8,
        "trace_session": 3,
        "overwritten": overwritten,
        "contention_dropped": contention_dropped,
        "internal_dropped": internal_dropped,
        "records": records,
    }


def _record(sequence: int) -> dict[str, JsonValue]:
    return {
        "actor_id": 42,
        "event": "force_accepted",
        "reason": "accepted",
        "trace_session": 3,
        "event_sequence": sequence,
        "native_steady_ns": sequence * 100,
        "optional_false": False,
        "optional_none": None,
        "optional_zero": 0,
        "transform": carla.Transform(
            carla.Location(x=1.0, y=2.0, z=3.0),
            carla.Rotation(pitch=4.0, yaw=5.0, roll=6.0),
        ),
    }


def _attestation(tmp_path: Path) -> RuntimeAttestation:
    return RuntimeAttestation(8000, 7, tmp_path / "libcarla.pyd", "E4B2")


def _context(phase: list[str] | None = None) -> NativeTrafficManagerDiagnosticsContext:
    phases = ["MODULE_1"] if phase is None else phase
    mono = IncrementingNanoseconds(1_000)
    utc = IncrementingNanoseconds(2_000)
    return NativeTrafficManagerDiagnosticsContext(
        "R-0042",
        "run-0042",
        "MODULE_1_RUNNING",
        SessionClock(HostClockTimestamp(1_000, 2_000)),
        lambda: phases[0],
        mono,
        utc,
    )


def _observation() -> VehicleObservation:
    return VehicleObservation(
        TimestampEnvelope(
            HostClockTimestamp(1_100, 2_100),
            CarlaSnapshotTimestamp(12.5, 321, 1_080, 1_090),
        ),
        VehicleState(12.5, 30.0, 0.0, 0.0, 0.0, -1, "off"),
    )


def _rows(path: Path) -> list[dict[str, JsonValue]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _open(
    manager: FakeDiagnosticTrafficManager,
    tmp_path: Path,
    *,
    context: NativeTrafficManagerDiagnosticsContext | None = None,
) -> NativeTrafficManagerDiagnosticsCollector:
    return NativeTrafficManagerDiagnosticsCollector.open(
        manager,
        actor_id=42,
        output_dir=tmp_path,
        attestation=_attestation(tmp_path),
        config=NativeTrafficManagerDiagnosticsConfig(capacity=8, interval_seconds=0.5),
        context=_context() if context is None else context,
        monotonic_clock=iter((0.0, 0.5, 0.9)).__next__,
    )


def test_collector_writes_identified_timed_rows_and_preserves_native_payload(
    tmp_path: Path,
) -> None:
    phases = ["MODULE_1"]
    manager = FakeDiagnosticTrafficManager(
        [
            _snapshot([_record(2), _record(1)]),
            _snapshot([_record(1), _record(3), _record(2)]),
            _snapshot([_record(1), _record(3), _record(2)]),
            _snapshot([_record(1), _record(3), _record(2)], enabled=False),
        ]
    )
    collector = _open(manager, tmp_path, context=_context(phases))
    phases[0] = "FINAL_HAZARD_ASSESSMENT"

    collector.poll(_observation())
    collector.close()

    rows = _rows(collector.path)
    assert all(
        row["participant_id"] == "R-0042" and row["run_id"] == "run-0042"
        for row in rows
    )
    records = [row for row in rows if row["kind"] == "native_record"]
    assert [row["native_payload"]["event_sequence"] for row in records] == [2, 1, 3]
    assert records[0]["native_payload"]["optional_false"] is False
    assert records[0]["native_payload"]["optional_none"] is None
    assert records[0]["native_payload"]["optional_zero"] == 0
    assert records[0]["native_payload"]["transform"]["rotation"] == {
        "pitch": 4.0,
        "roll": 6.0,
        "yaw": 5.0,
    }
    periodic = next(row for row in rows if row.get("phase") == "periodic")
    assert (
        periodic["read_begin_host_monotonic_ns"]
        < periodic["read_end_host_monotonic_ns"]
    )
    assert periodic["carla_frame"] == 321
    assert periodic["clock_mapping"] == "READ_RECEIPT_ONLY_NO_NATIVE_CLOCK_MAPPING"
    assert rows[-1]["coverage_status"] == "COMPLETE"
    assert rows[-1]["coverage_complete"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selected_actor", 7),
        ("capacity", 7),
        ("trace_session", 0),
        ("overwritten", -1),
        ("records", "not-a-list"),
    ],
)
def test_collector_rejects_snapshot_invariant_mismatch(
    tmp_path: Path,
    field: str,
    value: JsonValue,
) -> None:
    snapshot = _snapshot([])
    snapshot[field] = value

    with pytest.raises(NativeTrafficManagerDiagnosticsSchemaError):
        _open(FakeDiagnosticTrafficManager([snapshot]), tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_sequence", True),
        ("event_sequence", 0),
        ("native_steady_ns", -1),
        ("event", None),
        ("reason", False),
        ("actor_id", 7),
        ("trace_session", 4),
    ],
)
def test_collector_rejects_malformed_native_record(
    tmp_path: Path,
    field: str,
    value: JsonValue,
) -> None:
    record = _record(1)
    record[field] = value

    with pytest.raises(NativeTrafficManagerDiagnosticsSchemaError):
        _open(FakeDiagnosticTrafficManager([_snapshot([record])]), tmp_path)


def test_header_write_failure_never_enables_or_disables(tmp_path: Path) -> None:
    manager = FakeDiagnosticTrafficManager([])

    class HeaderFailure(io.StringIO):
        def write(self, value: str) -> int:
            raise OSError("header failed")

    with pytest.raises(OSError, match="header failed"):
        NativeTrafficManagerDiagnosticsCollector.open(
            manager,
            actor_id=42,
            output_dir=tmp_path,
            attestation=_attestation(tmp_path),
            config=NativeTrafficManagerDiagnosticsConfig(capacity=8),
            context=_context(),
            monotonic_clock=lambda: 0.0,
            stream_factory=lambda _path: HeaderFailure(),
        )

    assert manager.calls == []


def test_enable_failure_disables_exactly_once_and_closes_stream(tmp_path: Path) -> None:
    manager = FakeDiagnosticTrafficManager([])
    manager.enable_error = RuntimeError("enable failed")
    stream = io.StringIO()

    with pytest.raises(RuntimeError, match="enable failed"):
        NativeTrafficManagerDiagnosticsCollector.open(
            manager,
            actor_id=42,
            output_dir=tmp_path,
            attestation=_attestation(tmp_path),
            config=NativeTrafficManagerDiagnosticsConfig(capacity=8),
            context=_context(),
            monotonic_clock=lambda: 0.0,
            stream_factory=lambda _path: stream,
        )

    assert manager.calls == [("enable", 42, 8), ("disable",)]
    assert stream.closed


def test_counter_rollback_latches_failure_and_close_cannot_report_complete(
    tmp_path: Path,
) -> None:
    manager = FakeDiagnosticTrafficManager(
        [
            _snapshot([], overwritten=2),
            _snapshot([], overwritten=1),
            _snapshot([], overwritten=2),
            _snapshot([], enabled=False, overwritten=2),
        ]
    )
    collector = _open(manager, tmp_path)

    with pytest.raises(NativeTrafficManagerDiagnosticsSchemaError):
        collector.poll(_observation())
    with pytest.raises(NativeTrafficManagerDiagnosticsSchemaError):
        collector.close()

    assert collector.status.coverage_status == "FAILED_OBSERVER"
    assert collector.status.coverage_complete is False


def test_close_attempts_every_phase_and_preserves_first_error(tmp_path: Path) -> None:
    class CloseFailureManager(FakeDiagnosticTrafficManager):
        def __init__(self) -> None:
            super().__init__([_snapshot([]), _snapshot([], enabled=False)])
            self.reads = 0

        def read_diagnostic_trace(self) -> dict[str, JsonValue]:
            self.calls.append(("read",))
            self.reads += 1
            if self.reads == 2:
                raise RuntimeError("final enabled failed")
            return self.snapshots.pop(0)

        def disable_diagnostic_trace(self) -> None:
            self.calls.append(("disable",))
            raise OSError("disable failed")

    class CloseFailureStream(io.StringIO):
        def close(self) -> None:
            raise OSError("stream close failed")

    manager = CloseFailureManager()
    stream = CloseFailureStream()
    collector = NativeTrafficManagerDiagnosticsCollector.open(
        manager,
        actor_id=42,
        output_dir=tmp_path,
        attestation=_attestation(tmp_path),
        config=NativeTrafficManagerDiagnosticsConfig(capacity=8),
        context=_context(),
        monotonic_clock=lambda: 0.0,
        stream_factory=lambda _path: stream,
    )

    with pytest.raises(RuntimeError, match="final enabled failed") as caught:
        collector.close()

    assert manager.calls[-3:] == [("read",), ("disable",), ("read",)]
    assert collector._closed is True
    assert collector.status.coverage_status == "FAILED_OBSERVER"
    notes = getattr(caught.value, "__notes__", [])
    assert any("disable failed" in note for note in notes)
    assert any("stream close failed" in note for note in notes)


def test_collector_uses_exclusive_artifact_creation(tmp_path: Path) -> None:
    path = tmp_path / "native_tm_diagnostics.MODULE_1_RUNNING.ego-42.jsonl"
    path.write_text("protected", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _open(FakeDiagnosticTrafficManager([]), tmp_path)
    assert path.read_text(encoding="utf-8") == "protected"


@pytest.mark.parametrize("capacity", [False, 0, 4097])
def test_config_rejects_capacity_outside_native_range(capacity: int) -> None:
    with pytest.raises(NativeTrafficManagerDiagnosticsConfigError):
        NativeTrafficManagerDiagnosticsConfig(capacity=capacity)


@pytest.mark.parametrize("interval", [0.049, 5.001, float("inf"), float("nan")])
def test_config_rejects_invalid_poll_interval(interval: float) -> None:
    with pytest.raises(NativeTrafficManagerDiagnosticsConfigError):
        NativeTrafficManagerDiagnosticsConfig(interval_seconds=interval)
