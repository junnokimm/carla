import json

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.assignment import development_assignment
from src.p8.manifest import inspect_outputs
from src.p8.model import Mode, P8Error, Stage
from src.p8.orchestrator import SessionManager
from src.p8.runner import run_development


@pytest.mark.parametrize(
    "stage",
    [
        Stage.MODULE_1_PREPARE,
        Stage.MODULE_1_RUNNING,
        Stage.REST,
        Stage.MODULE_2_RUNNING,
        Stage.FINAL_HAZARD,
        Stage.POST_EXPERIMENT,
    ],
)
def test_abort_at_each_boundary_keeps_partial_files_and_never_completes(
    tmp_path, stage
):
    session = SessionManager(tmp_path).create(development_assignment(), "abort-run")

    result = run_development(session, abort_at=stage)

    assert result["completion_status"] == "ABORTED"
    assert result["state"] == "ABORTED"
    assert result["abort_reason"] == "DEV_ABORT_INJECTION:" + stage.value
    assert result["cleanup"]["order"] == [
        "LOGGER_FLUSH",
        "OWNED_RESOURCE_CLEANUP",
        "SESSION_STATUS_PERSIST",
    ]
    assert result["cleanup"]["completed"]
    assert result["components"]["current_noa_mode"] == "MANUAL"
    assert result["components"]["owned_resource_tokens"] == []
    assert result["completeness"]["software_artifacts_complete"] is False
    assert session.services.logger.event_path.stat().st_size > 0
    saved = json.loads(
        (session.services.directory / "session_manifest.json").read_text()
    )
    assert saved["state"] == "ABORTED"


def test_two_consecutive_participants_reset_all_runtime_state(tmp_path):
    manager = SessionManager(tmp_path)
    first = manager.create(
        development_assignment(Module1Condition.SURT, Module2Condition.NOA_L2), "first"
    )
    result_a = run_development(first)
    second = manager.create(
        development_assignment(Module1Condition.NO_SURT, Module2Condition.MANUAL),
        "second",
    )
    initial = second.initial_components

    assert initial["modules"] == {}
    assert initial["current_noa_mode"] == "MANUAL"
    assert initial["exit_slots"] == {}
    assert initial["calibration_status"] == "NOT_TESTED"
    assert initial["tablet_state"] == "IDLE"
    assert initial["tablet_trial"] is None
    assert initial["tablet_event_count"] == 0
    assert initial["hazard_state"] == "IDLE"
    assert initial["hazard_metrics"] is None
    assert initial["owned_resource_tokens"] == []
    assert initial["post"]["survey"] == "NOT_IMPLEMENTED"
    assert second.services.timeline.elapsed_ns == 0
    assert first.services.clock is not second.services.clock
    result_b = run_development(second)
    assert result_a["initial_components"]["modules"] == {}
    assert result_b["initial_components"]["modules"] == {}
    assert (
        result_a["components"]["tablet_epoch"] != result_b["components"]["tablet_epoch"]
    )
    assert result_b["components"]["modules"]["MODULE_2"]["initial_mode"] == "MANUAL"
    assert first.components.hazard.metrics is not second.components.hazard.metrics
    assert manager.occurrences.directory.exists()


def test_same_participant_new_run_does_not_reset_persistent_hazard_claim(tmp_path):
    manager = SessionManager(tmp_path)
    assignment = development_assignment()
    run_development(manager.create(assignment, "first"))
    second = manager.create(assignment, "second")

    with pytest.raises(RuntimeError, match="ALREADY"):
        run_development(second)

    assert second.result["state"] == "ABORTED"
    assert second.result["components"]["hazard_count"] == 0


def test_cleanup_failure_blocks_next_participant_and_cannot_complete(
    tmp_path, monkeypatch
):
    manager = SessionManager(tmp_path)
    session = manager.create(development_assignment(), "failed-cleanup")

    def fail_cleanup():
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(session.components, "cleanup", fail_cleanup)
    result = run_development(session)

    assert result["completion_status"] == "ABORTED"
    assert result["cleanup"]["completed"] is False
    with pytest.raises(P8Error, match="PREVIOUS"):
        manager.create(development_assignment(), "must-not-start")


def test_participant_path_blocks_before_any_dev_or_hardware_adapter(tmp_path):
    manager = SessionManager(tmp_path, mode=Mode.PARTICIPANT)

    with pytest.raises(P8Error, match="PARTICIPANT_BLOCKED"):
        manager.create(development_assignment(), "blocked")

    assert manager.active.components is None
    assert manager.active.closed
    assert not list((tmp_path / "blocked").glob("p6_*.jsonl"))
    assert not manager.occurrences.directory.exists()


def test_session_manager_rejects_dev_live_mode_before_creating_run_directory(tmp_path):
    manager = SessionManager(tmp_path, mode=Mode.DEV_LIVE_DRIVE)

    with pytest.raises(P8Error, match="DEV_LIVE_REQUIRES_DEDICATED_RUNNER"):
        manager.create(development_assignment(), "wrong-runner")

    assert not (tmp_path / "wrong-runner").exists()


def test_no_overwrite_and_manifest_detects_removed_raw_output(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.create(development_assignment(), "unique")
    run_development(session)
    with pytest.raises(FileExistsError):
        manager.create(development_assignment(), "unique")
    session.components.p6.gaze.path.unlink()

    audit = inspect_outputs(
        session.services.directory,
        session.services.context,
        complete=True,
        components=session.components.snapshot(),
    )

    assert audit["software_artifacts_complete"] is False


def test_output_inspection_preserves_explicit_live_provenance(tmp_path):
    session = SessionManager(tmp_path).create(development_assignment(), "provenance")
    session.services.close()

    audit = inspect_outputs(
        session.services.directory,
        session.services.context,
        complete=False,
        components={},
        provenance="REAL_CARLA_RUNTIME_DEV_PARTIAL",
    )

    assert audit["files"]
    assert all(
        item["provenance"] == "REAL_CARLA_RUNTIME_DEV_PARTIAL"
        for item in audit["files"]
    )


def test_component_cannot_enter_hazard_outside_orchestrator_stage(tmp_path):
    session = SessionManager(tmp_path).create(development_assignment(), "wrong-stage")
    try:
        with pytest.raises(P8Error, match="CONTEXT"):
            session.components.enter(Stage.FINAL_HAZARD)
        assert session.components.hazard is None
    finally:
        session.abort("TEST_END")


def test_logging_failure_during_abort_still_cleans_resources_and_persists_status(
    tmp_path, monkeypatch
):
    session = SessionManager(tmp_path).create(development_assignment(), "log-failure")
    original = session.services.emit

    def fail_flush_event(name, payload=None, **kwargs):
        if name == "p8_logs_flushed_before_cleanup":
            raise OSError("injected event writer failure")
        return original(name, payload, **kwargs)

    monkeypatch.setattr(session.services, "emit", fail_flush_event)

    session.abort("OPERATOR_REQUEST")

    assert session.components.clean
    assert session.closed
    assert session.result["completion_status"] == "ABORTED"
    assert session.result["cleanup"]["errors"]
