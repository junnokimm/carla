from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.p8.assignment import development_assignment
from src.p8.manifest import inspect_outputs
from src.p8.model import Stage
from src.p8.native_tm_diagnostics import (
    NativeTrafficManagerDiagnosticsSchemaError,
    NativeTrafficManagerDiagnosticsStatus,
)
from src.p8.operator_model import TrainingRun
from src.p8.orchestrator import SessionManager
from tests.test_p8_native_tm_diagnostics import (
    FakeDiagnosticTrafficManager,
    _open,
    _record,
    _snapshot,
)
from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev
from tests.test_p8_traffic_manager import _tm_runner_with_client


def test_collector_rejects_nan_native_payload(tmp_path: Path) -> None:
    record = _record(1)
    record["invalid_float"] = float("nan")
    manager = FakeDiagnosticTrafficManager([_snapshot([record])])

    with pytest.raises(ValueError, match="Out of range float values"):
        _open(manager, tmp_path)

    assert manager.calls == [("enable", 42, 8), ("read",), ("disable",)]


def test_collector_rejects_unsupported_native_payload(tmp_path: Path) -> None:
    class UnsupportedPayload:
        pass

    record = _record(1)
    record["unsupported"] = UnsupportedPayload()
    manager = FakeDiagnosticTrafficManager([_snapshot([record])])

    with pytest.raises(
        NativeTrafficManagerDiagnosticsSchemaError,
        match="contains unsupported UnsupportedPayload",
    ):
        _open(manager, tmp_path)

    assert manager.calls == [("enable", 42, 8), ("read",), ("disable",)]


def test_native_diagnostics_are_disabled_by_default_for_real_runner() -> None:
    runner, _, _, _ = _tm_runner_with_client()

    with runner.session() as session:
        assert session.native_tm_collector is None

    assert runner.native_tm_diagnostics_status is None


def test_failed_run_emits_failed_native_tm_status_after_cleanup(tmp_path: Path) -> None:
    controller, evidence = register_dev(tmp_path, StrictClock())
    evidence.failure = NativeTrafficManagerDiagnosticsSchemaError(
        "actor", "operator diagnostics mismatch"
    )
    evidence.cleanup_failure = RuntimeError("operator diagnostics cleanup failed")
    evidence.diagnostic_status = NativeTrafficManagerDiagnosticsStatus(
        "native_tm_diagnostics.TRAINING.ego-42.jsonl",
        42,
        "TRAINING",
        "TRAINING",
        9,
        "FAILED_OBSERVER",
        False,
        1,
        0,
        0,
        0,
        "RuntimeError:native diagnostic read failed",
    )
    advance_to(controller, Stage.TRAINING)

    with pytest.raises(NativeTrafficManagerDiagnosticsSchemaError) as caught:
        controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)

    assert evidence.viewers[-1].close_count == 1
    assert any(
        "operator diagnostics cleanup failed" in note
        for note in getattr(caught.value, "__notes__", ())
    )
    with controller.session.services.logger.event_path.open(
        newline="", encoding="utf-8"
    ) as source:
        status_rows = [
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "p8_native_tm_diagnostics_status"
        ]
    assert len(status_rows) == 1
    payload = json.loads(status_rows[0]["event_value"])
    assert payload["coverage_status"] == "FAILED_OBSERVER"
    assert payload["coverage_complete"] is False
    assert payload["first_error"] == "RuntimeError:native diagnostic read failed"


def test_output_inspection_accepts_native_tm_diagnostic_jsonl(tmp_path: Path) -> None:
    session = SessionManager(tmp_path).create(development_assignment(), "diagnostic")
    context = session.services.context
    sidecar = session.services.directory / (
        "native_tm_diagnostics.MODULE_2_RUNNING.ego-42.jsonl"
    )
    rows = [
        {
            "kind": kind,
            "participant_id": str(context.participant_id),
            "run_id": str(context.study_run_id),
        }
        for kind in ("header", "final_summary")
    ]
    sidecar.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    session.services.close()

    audit = inspect_outputs(
        session.services.directory,
        context,
        complete=False,
        components={},
    )

    diagnostic = next(item for item in audit["files"] if item["path"] == sidecar.name)
    assert diagnostic["data_rows"] == 2
    assert audit["issues"] == []
