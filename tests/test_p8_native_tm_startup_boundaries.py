from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from src.p8.model import Mode
from src.p8.native_tm_diagnostics_jsonl import NativeTrafficManagerDiagnosticsJsonl
from src.p8.native_tm_diagnostics_types import JsonValue
from src.p8.operator_cli import _abort_open_session
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import OperatorRuntimeConfig
from src.scenario.research_noa_runtime import ResearchNoAPreflightError
from tests import test_p8_native_tm_diagnostics_runtime as diagnostic_runtime
from tests.research_noa_fakes import FakeWorldSnapshot
from tests.test_p8_native_tm_diagnostics_runtime import (
    PassiveDiagnosticTrafficManager,
)
from tests.test_p8_native_tm_diagnostics_runtime import (
    test_nominal_module_two_handoff_uses_one_collector_through_final_cancel as run_operator,
)
from tests.test_p8_native_tm_inventory import inventory_events, inventory_runner


def test_inventory_wait_is_capped_even_with_large_rpc_timeout(tmp_path: Path) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    runner._config = replace(runner._config, timeout=60.0)
    world.visible_frame = 1000
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_REJECTED"),
        runner.session(),
    ):
        pytest.fail("missing ego must fail closed")
    assert world.clock() == pytest.approx(5.0)
    assert max(world.wait_for_tick_calls) <= 5.0
    assert not manager.enabled
    assert inventory_events(tmp_path)[-1]["timeout_seconds"] == 5.0


def test_snapshot_failure_records_unavailable_frame_and_cleans_ego(
    tmp_path: Path,
) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.snapshot_error_at_call = 1
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_FAILED"),
        runner.session(),
    ):
        pytest.fail("snapshot failure must fail closed")
    row = inventory_events(tmp_path)[-1]
    assert row["frame_before"] is None
    assert row["reason"] == "INVENTORY_QUERY_FAILED"
    assert not manager.enabled
    assert world.vehicle.destroy_count == 1


def test_returned_tick_without_local_state_progress_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    monkeypatch.setattr(world, "wait_for_tick", lambda seconds: FakeWorldSnapshot(10))
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_REJECTED"),
        runner.session(),
    ):
        pytest.fail("old local state must not pass")
    assert inventory_events(tmp_path)[-1]["reason"] == "FRAME_NOT_ADVANCED"
    assert not manager.enabled


def test_runner_startup_interrupt_records_failure_and_cleans_spawned_ego(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.visible_frame = 0
    interrupt = KeyboardInterrupt("enable interrupted")
    disabled: list[bool] = []

    def enable(actor_id: int, capacity: int) -> None:
        raise interrupt

    monkeypatch.setattr(manager, "enable_diagnostic_trace", enable)
    monkeypatch.setattr(
        manager, "disable_diagnostic_trace", lambda: disabled.append(True)
    )
    with pytest.raises(KeyboardInterrupt) as caught, runner.session():
        pytest.fail("interrupted startup must not continue")
    assert caught.value is interrupt
    assert disabled == [True]
    assert world.vehicle.destroy_count == 1
    assert runner.native_tm_diagnostics_status is not None
    assert runner.native_tm_diagnostics_status.coverage_status == "FAILED_OBSERVER"


def test_runner_context_interrupt_marks_collection_incomplete(tmp_path: Path) -> None:
    runner, world, _ = inventory_runner(tmp_path)
    world.visible_frame = 0
    interrupt = KeyboardInterrupt("body interrupted")
    with pytest.raises(KeyboardInterrupt) as caught, runner.session() as session:
        raise interrupt
    assert caught.value is interrupt
    assert world.vehicle.destroy_count == 1
    assert session.native_tm_collector is not None
    assert session.native_tm_collector.status.coverage_status == "FAILED_OBSERVER"


@pytest.mark.parametrize("phase", ["disable", "summary"])
def test_operator_persists_real_collector_finalization_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    interrupt = KeyboardInterrupt("finalization interrupted")
    original_write = NativeTrafficManagerDiagnosticsJsonl.write
    controllers: list[OperatorController] = []

    def capture_controller(
        output_root: Path,
        *,
        mode: Mode,
        runtime: OperatorRuntimeConfig,
        monotonic_ns: Callable[[], int],
        utc_ns: Callable[[], int],
    ) -> OperatorController:
        controller = OperatorController(
            output_root,
            mode=mode,
            runtime=runtime,
            monotonic_ns=monotonic_ns,
            utc_ns=utc_ns,
        )
        controllers.append(controller)
        return controller

    monkeypatch.setattr(diagnostic_runtime, "OperatorController", capture_controller)

    def disable(manager: PassiveDiagnosticTrafficManager) -> None:
        manager.enabled = False
        raise interrupt

    def write(
        sink: NativeTrafficManagerDiagnosticsJsonl, row: Mapping[str, JsonValue]
    ) -> None:
        original_write(sink, row)
        if row["kind"] == "final_summary":
            raise interrupt

    if phase == "disable":
        monkeypatch.setattr(
            PassiveDiagnosticTrafficManager, "disable_diagnostic_trace", disable
        )
    else:
        monkeypatch.setattr(NativeTrafficManagerDiagnosticsJsonl, "write", write)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_operator(tmp_path)
    assert caught.value is interrupt
    _abort_open_session(controllers[0], "OPERATOR_CLI_INTERRUPTED")
    event_path = next(tmp_path.rglob("research_events_*.csv"))
    with event_path.open(encoding="utf-8", newline="") as source:
        statuses = [
            json.loads(row["event_value"])
            for row in csv.DictReader(source)
            if row["event_type"] == "p8_native_tm_diagnostics_status"
        ]
    assert len(statuses) == 1
    assert statuses[0]["coverage_status"] == "FAILED_OBSERVER"
    assert statuses[0]["coverage_complete"] is False
    manifest = json.loads(next(tmp_path.rglob("session_manifest.json")).read_text())
    assert manifest["state"] == "ABORTED"
    assert manifest["cleanup"]["completed"] is True
