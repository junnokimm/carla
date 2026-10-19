from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import P8Error, Stage
from src.p8.operator_model import DevOutcome
from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev


@pytest.mark.parametrize("m1", list(Module1Condition))
@pytest.mark.parametrize("m2", list(Module2Condition))
def test_operator_records_blocked_hazard_after_separate_module_window(
    tmp_path: Path, m1: Module1Condition, m2: Module2Condition
) -> None:
    # Given: the existing operator controller with fake, not network, runners.
    controller, evidence = register_dev(tmp_path, StrictClock(), m1, m2)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module()
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    controller.advance()
    controller.advance()
    controller.advance()
    controller.advance()
    module_two = controller.execute_current_module()
    services = controller.session.services

    # When: the synchronously entered final-hazard binding is entered again.
    controller.session.components.enter(Stage.FINAL_HAZARD)
    controller.advance()
    controller.abort("offline partial data verification")

    # Then: one unobservable record is retained on the actual M2 runtime.
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    hazard_rows = [row for row in rows if row["event_type"] == "hazard_observation"]
    assert len(hazard_rows) == 1
    row = hazard_rows[0]
    payload = json.loads(row["event_value"])
    assert row["phase"] == "FINAL_HAZARD_ASSESSMENT"
    assert row["participant_id"] == "R-0042"
    assert payload["run_id"] == "operator-run"
    assert payload["m2_condition"] == m2.value
    assert payload["auto_state"] == (
        "MANUAL" if m2 is Module2Condition.MANUAL else "NOA_ACTIVE"
    )
    assert payload["execution_status"] == "BLOCKED"
    assert payload["observation_status"] == "NOT_OBSERVED"
    assert payload["hazard_outcome"] == "UNOBSERVABLE"
    assert all(
        payload[key] is None
        for key in (
            "hazard_visible_t",
            "brake_onset_t",
            "steer_onset_t",
            "ttc_at_response",
            "min_ttc",
            "hazard_start_speed",
            "hazard_start_distance",
        )
    )
    assert float(row["session_elapsed_s"]) == pytest.approx(
        payload["session_elapsed_s"]
    )
    assert float(row["session_elapsed_s"]) <= module_two.runtime_closed_session_s
    assert module_two.requested_duration_s == 780
    assert len(evidence.vehicles) == 3
    assert all(vehicle.destroy_count == 1 for vehicle in evidence.vehicles)
    assert not controller.session.components.occurrences.directory.exists()
    manifest = json.loads(
        (services.directory / "session_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["completion_status"] == "ABORTED"
    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["hazard_outcome"] == "UNOBSERVABLE"
    assert record["session_elapsed_s"] == payload["session_elapsed_s"]


@pytest.mark.parametrize("m2", list(Module2Condition))
def test_nominal_m2_enters_blocked_hazard_before_live_shutdown(
    tmp_path: Path,
    m2: Module2Condition,
) -> None:
    # Given: training and M1 have closed, and nominal M2 uses a real fake-world session.
    controller, evidence = register_dev(
        tmp_path, StrictClock(), Module1Condition.NO_SURT, m2
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(4):
        controller.advance()

    # When: nominal M2's viewer loop ends.
    controller.execute_current_module()

    # Then: FINAL_HAZARD was bound on the same live state before ordinary shutdown.
    assert controller.snapshot().stage is Stage.FINAL_HAZARD
    assert [viewer.close_count for viewer in evidence.viewers] == [1, 1, 1]
    assert [vehicle.destroy_count for vehicle in evidence.vehicles] == [1, 1, 1]
    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["module_2_runtime_status"] == "BOUND_SHARED_RUNTIME"
    assert record["binding_status"] == "BOUND"
    assert record["observation_readiness"] == "BLOCKED_UNAPPROVED"
    assert record["runtime_continuity_scope"] == "SAME_LIVE_SESSION_PRE_SHUTDOWN"
    assert record["control_activity_status"] == "LIVE_M2_STATE_AT_HAZARD_ENTRY"
    assert record["sensor_activity_status"] == "LIVE_VIEWER_BOUND_AT_HAZARD_ENTRY"
    assert record["auto_state"] == (
        "MANUAL" if m2 is Module2Condition.MANUAL else "NOA_ACTIVE"
    )
    assert record["shutdown_brake_status_at_binding"] == "NOT_ATTEMPTED"
    assert record["first_displayed_frame"] is None
    assert record["raw_observation"] is None
    assert record["candidate_observation"] is None
    identities = record["shared_resource_identities"]
    assert isinstance(identities, dict)
    assert identities["hero"] == id(evidence.vehicles[-1])
    assert identities["viewer_sensor_owner"] == id(evidence.viewers[-1])
    assert identities["logger"] == id(controller.session.services.logger)
    assert identities["session_clock"] == id(controller.session.services.clock)
    assert len(evidence.vehicles) == 3
    assert not controller.session.components.occurrences.directory.exists()
    module_stage = next(
        item
        for item in controller.session.records
        if item["stage"] == "MODULE_2_RUNNING"
    )
    hazard_stage = next(
        item for item in controller.session.records if item["stage"] == "FINAL_HAZARD"
    )
    assert module_stage["end_session_s"] <= hazard_stage["start_session_s"]
    with controller.session.services.logger.event_path.open(
        newline="", encoding="utf-8"
    ) as source:
        bounded_run_rows = [
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "p8_actual_bounded_run"
        ]
    module_two_row = bounded_run_rows[-1]
    module_two_payload = json.loads(module_two_row["event_value"])
    assert module_two_row["phase"] == "MODULE_2"
    assert module_two_payload["orchestration_stage"] == "MODULE_2_RUNNING"

    controller.advance()

    assert controller.snapshot().stage is Stage.POST_EXPERIMENT
    assert evidence.viewers[-1].close_count == 1
    assert evidence.vehicles[-1].destroy_count == 1
    hazard_stage = next(
        item for item in controller.session.records if item["stage"] == "FINAL_HAZARD"
    )
    assert hazard_stage["status"] == "BLOCKED"
    controller.abort("offline cleanup")
    assert evidence.viewers[-1].close_count == 1
    assert evidence.vehicles[-1].destroy_count == 1


def test_abort_at_blocked_hazard_does_not_repeat_normal_runtime_cleanup(
    tmp_path: Path,
) -> None:
    # Given: nominal M2 has handed the actual session to the guarded hazard boundary.
    controller, evidence = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(4):
        controller.advance()
    controller.execute_current_module()

    # When: the operator aborts before acknowledging the blocked hazard boundary.
    controller.abort("offline abort at guarded hazard")
    controller.abort("repeated abort is inert")

    # Then: the M2 viewer and hero are each released once by the P8 owner.
    assert controller.session.closed
    assert evidence.viewers[-1].close_count == 1
    assert evidence.vehicles[-1].destroy_count == 1
    assert controller.session.components.snapshot()["owned_resource_tokens"] == []


def test_nominal_m2_cleanup_failure_aborts_after_pre_shutdown_handoff(
    tmp_path: Path,
) -> None:
    # Given: the retained M2 viewer will fail after performing its close operation.
    controller, evidence = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(4):
        controller.advance()
    evidence.cleanup_failure = RuntimeError("retained viewer cleanup failed")

    # When: nominal M2 hands off before its ordinary context cleanup fails.
    with pytest.raises(RuntimeError, match="retained viewer cleanup failed"):
        controller.execute_current_module()

    # Then: keep the operator session open without retrying released resources.
    assert not controller.session.closed
    assert controller.session.state is Stage.FINAL_HAZARD
    assert evidence.viewers[-1].close_count == 1
    assert evidence.vehicles[-1].destroy_count == 1
    assert controller.session.components.snapshot()["owned_resource_tokens"] == []
    controller.abort("TEST_OPERATOR_STOP")


def test_blocked_hazard_requires_explicit_blocked_outcome_and_is_not_completed(
    tmp_path: Path,
) -> None:
    # Given: a fake operator session already at the final-hazard boundary.
    controller, _ = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(4):
        controller.advance()
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M2")
    controller.advance()

    # When: the guarded policy records BLOCKED rather than pretending a skip ran.
    controller.record_execution_policy_outcome(DevOutcome.BLOCKED, "hazard blocked")
    controller.advance()

    # Then: no physical hazard completion is asserted and stage timing is retained.
    assert controller.snapshot().stage is Stage.POST_EXPERIMENT
    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["hazard_outcome"] == "UNOBSERVABLE"
    assert record["execution_status"] == "BLOCKED"
    assert record["binding_status"] == "NOT_BOUND"
    assert record["runtime_continuity_scope"] == "NOT_BOUND"
    assert record["control_activity_status"] == "INACTIVE_NOT_BOUND"
    assert record["sensor_activity_status"] == "INACTIVE_NOT_BOUND"
    hazard_stage = next(
        item for item in controller.session.records if item["stage"] == "FINAL_HAZARD"
    )
    assert hazard_stage["end_session_s"] >= hazard_stage["start_session_s"]
    controller.abort("offline cleanup")


@pytest.mark.parametrize(
    ("stage", "duration"),
    [
        (Stage.MODULE_1_RUNNING, None),
        (Stage.MODULE_2_RUNNING, 1.0),
    ],
)
def test_non_nominal_m2_runs_do_not_enter_final_hazard(
    tmp_path: Path, stage: Stage, duration: float | None
) -> None:
    # Given: an operator session at M1 or M2 with fake live resources.
    controller, _ = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.MODULE_1_RUNNING)
    if stage is Stage.MODULE_2_RUNNING:
        controller.execute_current_module(1.0)
        controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
        for _ in range(4):
            controller.advance()

    # When: M1 or a bounded partial M2 completes.
    controller.execute_current_module(duration)

    # Then: no implicit final-hazard handoff occurred.
    assert controller.snapshot().stage is stage
    assert controller.session.components.snapshot()["hazard_observation"] is None
    controller.abort("offline cleanup")


@pytest.mark.parametrize("failure_kind", ["USER_EXIT", "RUNTIME_ERROR"])
def test_failed_nominal_m2_does_not_enter_final_hazard(
    tmp_path: Path, failure_kind: str
) -> None:
    # Given: M1 is complete and nominal M2 will exit or raise inside the viewer loop.
    controller, evidence = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(4):
        controller.advance()
    if failure_kind == "USER_EXIT":
        evidence.user_exit = True
        expected_error = P8Error
        expected = "USER_EXIT_ABORTED_OPERATING_SESSION"
    else:
        evidence.failure = RuntimeError("injected M2 viewer failure")
        expected_error = RuntimeError
        expected = "injected M2 viewer failure"

    # When: nominal M2 does not complete normally.
    with pytest.raises(expected_error, match=expected):
        controller.execute_current_module()

    # Only an explicit user exit closes the session; runtime errors await the operator.
    assert controller.session.state is (
        Stage.ABORTED if failure_kind == "USER_EXIT" else Stage.MODULE_2_RUNNING
    )
    assert controller.session.components.snapshot()["hazard_observation"] is None
    if not controller.session.closed:
        controller.abort("TEST_OPERATOR_STOP")
