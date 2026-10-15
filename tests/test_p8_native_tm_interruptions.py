from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest

from src.p8.native_tm_diagnostics import (
    JsonValue,
    NativeTrafficManagerDiagnosticsCollector,
    NativeTrafficManagerDiagnosticsConfig,
    NativeTrafficManagerDiagnosticsContext,
)
from src.scenario.research_noa_lifecycle import retain_first_error
from tests.test_p8_native_tm_diagnostics import (
    FakeDiagnosticTrafficManager,
    _attestation,
    _context,
    _observation,
    _snapshot,
)


class InterruptingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0
        self.interrupt_close = False
        self.interrupt_summary_flush = False
        self.writes: list[str] = []

    def write(self, value: str) -> int:
        self.writes.append(value)
        return super().write(value)

    def flush(self) -> None:
        if self.interrupt_summary_flush and '"kind":"final_summary"' in self.writes[-1]:
            raise KeyboardInterrupt("summary flush interrupted")
        super().flush()

    def close(self) -> None:
        self.close_calls += 1
        if self.interrupt_close:
            raise KeyboardInterrupt("stream close interrupted")
        super().close()


class InterruptingManager(FakeDiagnosticTrafficManager):
    def __init__(self, snapshots: list[dict[str, JsonValue]]) -> None:
        super().__init__(snapshots)
        self.interrupt_enable = False
        self.interrupt_disable = False
        self.interrupt_read_at: int | None = None
        self.read_count = 0

    def enable_diagnostic_trace(self, actor_id: int, capacity: int) -> None:
        self.calls.append(("enable", actor_id, capacity))
        if self.interrupt_enable:
            raise KeyboardInterrupt("enable interrupted")

    def read_diagnostic_trace(self) -> dict[str, JsonValue]:
        self.calls.append(("read",))
        self.read_count += 1
        if self.read_count == self.interrupt_read_at:
            raise KeyboardInterrupt(f"read {self.read_count} interrupted")
        return self.snapshots.pop(0)

    def disable_diagnostic_trace(self) -> None:
        self.calls.append(("disable",))
        if self.interrupt_disable:
            raise KeyboardInterrupt("disable interrupted")


def open_collector(
    manager: InterruptingManager,
    tmp_path: Path,
    stream: InterruptingStream,
    *,
    clock: Callable[[], float] = lambda: 0.0,
    context: NativeTrafficManagerDiagnosticsContext | None = None,
) -> NativeTrafficManagerDiagnosticsCollector:
    return NativeTrafficManagerDiagnosticsCollector.open(
        manager,
        actor_id=42,
        output_dir=tmp_path,
        attestation=_attestation(tmp_path),
        config=NativeTrafficManagerDiagnosticsConfig(capacity=8, interval_seconds=0.5),
        context=_context() if context is None else context,
        monotonic_clock=clock,
        stream_factory=lambda _path: stream,
    )


@pytest.mark.parametrize("phase", ["clock", "header", "enable", "initial"])
def test_open_interrupt_closes_stream_and_only_enabled_trace_is_disabled(
    tmp_path: Path,
    phase: str,
) -> None:
    manager = InterruptingManager([_snapshot([])])
    stream = InterruptingStream()
    clock = lambda: 0.0
    context = _context()
    if phase == "clock":
        clock = lambda: (_ for _ in ()).throw(KeyboardInterrupt("clock interrupted"))
    elif phase == "header":
        context = NativeTrafficManagerDiagnosticsContext(
            context.participant_id,
            context.run_id,
            context.originating_stage,
            context.session_clock,
            lambda: (_ for _ in ()).throw(KeyboardInterrupt("header interrupted")),
            context.host_monotonic_ns,
            context.host_utc_ns,
        )
    elif phase == "enable":
        manager.interrupt_enable = True
    else:
        manager.interrupt_read_at = 1

    with pytest.raises(KeyboardInterrupt) as caught:
        open_collector(manager, tmp_path, stream, clock=clock, context=context)

    assert str(caught.value) == f"{phase} interrupted" or phase == "initial"
    assert stream.close_calls == 1
    if phase in {"enable", "initial"}:
        assert manager.calls[-1] == ("disable",)
    else:
        assert manager.calls == []


def test_periodic_read_interrupt_is_latched_and_never_reports_complete(
    tmp_path: Path,
) -> None:
    manager = InterruptingManager([_snapshot([])])
    stream = InterruptingStream()
    collector = open_collector(
        manager, tmp_path, stream, clock=iter((0.0, 0.5)).__next__
    )
    interrupt = KeyboardInterrupt("periodic interrupted")
    manager.read_diagnostic_trace = lambda: (_ for _ in ()).throw(interrupt)

    with pytest.raises(KeyboardInterrupt) as caught:
        collector.poll(_observation())

    assert caught.value is interrupt
    assert collector.status.coverage_status == "FAILED_OBSERVER"
    assert collector.status.coverage_complete is False


@pytest.mark.parametrize(
    "phase",
    ["final_enabled", "disable", "final_disabled", "summary_flush", "stream_close"],
)
def test_close_interrupt_attempts_every_remaining_phase_and_keeps_failed_status(
    tmp_path: Path,
    phase: str,
) -> None:
    manager = InterruptingManager(
        [_snapshot([]), _snapshot([]), _snapshot([], enabled=False)]
    )
    stream = InterruptingStream()
    collector = open_collector(manager, tmp_path, stream)
    if phase == "final_enabled":
        manager.interrupt_read_at = 2
    elif phase == "disable":
        manager.interrupt_disable = True
    elif phase == "final_disabled":
        manager.interrupt_read_at = 3
    elif phase == "summary_flush":
        stream.interrupt_summary_flush = True
    else:
        stream.interrupt_close = True

    with pytest.raises(KeyboardInterrupt) as caught:
        collector.close()

    assert "interrupted" in str(caught.value)
    assert manager.calls.count(("disable",)) == 1
    assert manager.read_count == 3
    assert stream.close_calls == 1
    assert collector._closed is True
    assert collector.status.coverage_status == "FAILED_OBSERVER"
    assert collector.status.coverage_complete is False
    if phase == "stream_close":
        assert '"artifact_durability":"PENDING_STREAM_CLOSE"' in stream.writes[-1]


def test_first_interrupt_identity_survives_secondary_cleanup_interrupts(
    tmp_path: Path,
) -> None:
    manager = InterruptingManager(
        [_snapshot([]), _snapshot([]), _snapshot([], enabled=False)]
    )
    stream = InterruptingStream()
    collector = open_collector(manager, tmp_path, stream)
    primary = KeyboardInterrupt("operator interrupted")
    collector.mark_interrupted(primary)
    manager.interrupt_disable = True
    stream.interrupt_close = True

    with pytest.raises(KeyboardInterrupt) as caught:
        collector.close()

    assert caught.value is primary
    assert any("disable interrupted" in note for note in primary.__notes__)
    assert any("stream close interrupted" in note for note in primary.__notes__)
    assert collector.status.payload()["cleanup_errors"] == primary.__notes__


def test_retain_first_error_catches_interrupt_and_continues_cleanup() -> None:
    primary = KeyboardInterrupt("cleanup interrupted")
    calls: list[str] = []

    retained = retain_first_error(lambda: (_ for _ in ()).throw(primary), None)
    retained = retain_first_error(lambda: calls.append("downstream"), retained)

    assert retained is primary
    assert calls == ["downstream"]
