import csv
import json

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.assignment import development_assignment
from src.p8.model import SEQUENCE, Mode, P8Error, Stage
from src.p8.orchestrator import SessionManager
from src.p8.runner import run_development


@pytest.mark.parametrize("m1", list(Module1Condition))
@pytest.mark.parametrize("m2", list(Module2Condition))
def test_four_combinations_use_actual_software_components_with_explicit_stubs(tmp_path, m1, m2):
    manager = SessionManager(tmp_path, mode=Mode.ACCELERATED)
    session = manager.create(development_assignment(m1, m2), "run-1")

    result = run_development(session)

    assert result["completion_status"] == "DEV_FLOW_COMPLETE"
    assert result["participant_run"] is False
    assert result["development_mode"] is True
    assert result["timing_profile"] == "DEV_ACCELERATED"
    assert result["participant_ready"] is False
    assert [record["stage"] for record in result["stages"]] == [stage.value for stage in SEQUENCE]
    modules = result["components"]["modules"]
    assert modules["MODULE_1"]["condition"] == m1.value
    assert modules["MODULE_2"]["condition"] == m2.value
    assert modules["MODULE_1"]["initial_mode"] == "MANUAL"
    assert modules["MODULE_2"]["initial_mode"] == ("MANUAL" if m2 is Module2Condition.MANUAL else "NOA_ACTIVE")
    assert modules["MODULE_1"]["automation_on_proportion"] == pytest.approx(0.5)
    assert result["components"]["exit_slots"] == {"TRAINING": 1, "MODULE_1": 1, "MODULE_2": 1}
    assert result["components"]["hazard_count"] == 1
    assert result["components"]["hazard_state"] == "COMPLETED"
    assert result["components"]["owned_resource_tokens"] == []
    assert result["components"]["post"]["survey"] == "DEV_STUB"
    assert result["components"]["post"]["real_survey_completed"] is False
    assert result["completeness"]["software_artifacts_complete"]
    assert session.components.hazard.clock is session.services.clock
    assert session.services.logger._session_clock is session.services.clock
    with session.services.logger.event_path.open(newline="") as source:
        events = list(csv.DictReader(source))
    assert {row["run_id"] for row in events} == {"run-1"}
    assert {row["participant_id"] for row in events} == {session.services.context.participant_id}
    hazard_rows = [row for row in events if row["event_type"] == "hazard_trigger"]
    assert len(hazard_rows) == 1
    assert hazard_rows[0]["phase"] == "FINAL_HAZARD_ASSESSMENT"


def test_cannot_skip_stages_or_complete_operator_steps_without_receipts(tmp_path):
    session = SessionManager(tmp_path).create(development_assignment(), "guard")
    try:
        with pytest.raises(P8Error, match="ORDER"):
            session.transition(Stage.FINAL_HAZARD)
        session.transition(Stage.CONSENT_AND_ELIGIBILITY)
        with pytest.raises(P8Error, match="RECEIPT"):
            session.transition(Stage.TRAINING)
    finally:
        session.abort("TEST_END")


def test_aborted_manifest_is_persisted_and_never_complete(tmp_path):
    session = SessionManager(tmp_path).create(development_assignment(), "aborted")

    session.abort("OPERATOR_REQUEST")

    manifest = json.loads((session.services.directory / "session_manifest.json").read_text())
    assert manifest["completion_status"] == "ABORTED"
    assert manifest["abort_reason"] == "OPERATOR_REQUEST"
    assert manifest["cleanup"]["completed"]
    assert session.closed
    with pytest.raises(P8Error):
        session.transition(Stage.CONSENT_AND_ELIGIBILITY)


def test_nominal_dry_profile_runs_virtual_v5_windows_without_changing_constants(tmp_path):
    session = SessionManager(tmp_path, mode=Mode.DRY).create(development_assignment(), "nominal-dry")

    result = run_development(session)

    durations = {record["stage"]: record["duration_s"] for record in result["stages"]}
    assert durations["MODULE_1_RUNNING"] == 600
    assert durations["REST"] == 120
    assert durations["MODULE_2_RUNNING"] == 780
    assert result["components"]["modules"]["MODULE_1"]["automation_on_seconds"] == 300
    assert result["timing_profile"] == "DEV_VIRTUAL_NOMINAL"
    assert result["participant_run"] is False
