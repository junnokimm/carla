from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import Module2Condition
from src.p8.model import Mode, P8Error, Stage
from src.p8.operator_execution import require_offline_hazard_runner
from src.p8.operator_hazard_contracts import HazardCleanupEvidence
from tests.p8_hazard_execution_support import (
    OfflineObservationError,
    StrictClock,
    advance_to_nominal_m2,
    controller_with_hazard,
    observation,
)


def test_nominal_m2_executes_existing_hazard_loop_on_same_resources(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    observations = [
        observation(0.0),
        observation(21.0),
        observation(22.0),
        observation(23.0, lateral=1.0, reveal=True),
        observation(
            24.0,
            lateral=3.5,
            reveal=True,
            complete=True,
            driver_input=DriverInput(brake=0.3),
        ),
        observation(25.0, lateral=3.5, reveal=True, complete=True),
        observation(26.0, lateral=3.5, reveal=True, complete=True),
        observation(27.0, lateral=3.5, reveal=True, complete=True),
        observation(28.0, lateral=3.5, reveal=True, complete=True),
    ]
    controller, evidence, backend, safety, owned, resources_seen = (
        controller_with_hazard(tmp_path, clock, observations)
    )
    advance_to_nominal_m2(controller)

    controller.execute_current_module()

    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["execution_status"] == "EXECUTED_OFFLINE"
    assert record["hazard_state"] == "COMPLETED"
    assert record["tick_count"] == 9
    assert record["first_displayed_frame"] is None
    assert record["approval_reference"] == "TEST-APPROVAL-1"
    assert safety.requests == []
    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]
    assert len(resources_seen) == 1
    resources = resources_seen[0]
    assert resources.session.world is resources_seen[0].session.world
    assert resources.session.hero is evidence.vehicles[-1]
    assert resources.session.viewer is evidence.viewers[-1]
    assert resources.identities.shutdown_brake_status == "NOT_ATTEMPTED"
    assert evidence.viewers[-1].attach_count == 1
    assert len(evidence.viewers[-1].control_modes_at_run) == 1
    assert evidence.viewers[-1].close_count == 1
    assert evidence.vehicles[-1].destroy_count == 1
    with controller.session.services.logger.event_path.open(
        newline="", encoding="utf-8"
    ) as source:
        rows = list(csv.DictReader(source))
    hazard_samples = [row for row in rows if row["event_type"] == "hazard_sample"]
    assert len(hazard_samples) >= 3
    assert {row["phase"] for row in hazard_samples} == {"FINAL_HAZARD_ASSESSMENT"}
    payloads = [json.loads(row["event_value"]) for row in hazard_samples]
    assert any(
        payload["driver_input"]["brake"] == 0.3
        and payload["input_source"] == "INJECTED_TEST_DRIVER_INPUT"
        for payload in payloads
    )
    hazard_stage_start = next(
        index
        for index, row in enumerate(rows)
        if row["event_type"] == "stage_start"
        and row["phase"] == "FINAL_HAZARD_ASSESSMENT"
    )
    first_hazard_sample = next(
        index for index, row in enumerate(rows) if row["event_type"] == "hazard_sample"
    )
    assert hazard_stage_start < first_hazard_sample
    stage_boundaries = [
        (row["phase"], row["event_type"])
        for row in rows
        if row["event_type"] in ("stage_start", "stage_end")
        and row["phase"] in ("MODULE_2", "FINAL_HAZARD_ASSESSMENT")
        and (
            row["event_value"] == "MODULE_2_RUNNING"
            or row["event_value"].startswith("MODULE_2_RUNNING:")
            or row["event_value"] == "FINAL_HAZARD"
            or row["event_value"].startswith("FINAL_HAZARD:")
        )
    ]
    hazard_boundary = stage_boundaries.index(("FINAL_HAZARD_ASSESSMENT", "stage_start"))
    assert stage_boundaries[hazard_boundary - 1 :] == [
        ("MODULE_2", "stage_end"),
        ("FINAL_HAZARD_ASSESSMENT", "stage_start"),
        ("FINAL_HAZARD_ASSESSMENT", "stage_end"),
    ]
    assert sum(row["event_type"] == "hazard_observation" for row in rows) == 1
    with controller.session.services.logger.telemetry_path.open(
        newline="", encoding="utf-8"
    ) as source:
        telemetry = list(csv.DictReader(source))
    assert sum(row["phase"] == "FINAL_HAZARD_ASSESSMENT" for row in telemetry) == 9
    first_hazard = next(
        index
        for index, row in enumerate(telemetry)
        if row["phase"] == "FINAL_HAZARD_ASSESSMENT"
    )
    assert all(
        row["phase"] == "FINAL_HAZARD_ASSESSMENT" for row in telemetry[first_hazard:]
    )
    module_2 = next(
        result
        for result in controller.session.components.runs
        if result.stage is Stage.MODULE_2_RUNNING
    )
    assert module_2.actual_active_drive_s == pytest.approx(780.0)


@pytest.mark.parametrize(
    ("mode", "client", "viewer", "input_source", "reason"),
    [
        (
            Mode.PARTICIPANT,
            "INJECTED_TEST_CLIENT",
            "INJECTED_TEST_VIEWER",
            "INJECTED_TEST_DRIVER_INPUT",
            "HAZARD_EXECUTION_REQUIRES_DEV_REHEARSAL",
        ),
        (
            Mode.DEV_REHEARSAL,
            "REAL_CARLA_RPC",
            "INJECTED_TEST_VIEWER",
            "INJECTED_TEST_DRIVER_INPUT",
            "HAZARD_EXECUTION_REQUIRES_INJECTED_TEST_CLIENT",
        ),
        (
            Mode.DEV_REHEARSAL,
            "INJECTED_TEST_CLIENT",
            "REAL_DRIVER_VIEW",
            "INJECTED_TEST_DRIVER_INPUT",
            "HAZARD_EXECUTION_REQUIRES_INJECTED_TEST_VIEWER",
        ),
        (
            Mode.DEV_REHEARSAL,
            "INJECTED_TEST_CLIENT",
            "INJECTED_TEST_VIEWER",
            "REAL_KEYBOARD_PATH",
            "HAZARD_EXECUTION_REQUIRES_INJECTED_TEST_DRIVER_INPUT",
        ),
    ],
)
def test_hazard_runner_rejects_every_non_offline_surface(
    mode: Mode,
    client: str,
    viewer: str,
    input_source: str,
    reason: str,
) -> None:
    runner = SimpleNamespace(
        client_source=client,
        viewer_source=viewer,
        driver_input_source=input_source,
    )

    with pytest.raises(P8Error, match=reason):
        require_offline_hazard_runner(mode, runner)


def test_collision_requests_safety_owner_without_claiming_physical_stop(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    observations = [
        observation(0.0),
        observation(1.0, collision_actor_id=501),
    ]
    controller, _, backend, safety, owned, _ = controller_with_hazard(
        tmp_path, clock, observations
    )
    advance_to_nominal_m2(controller)

    controller.execute_current_module()

    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["hazard_state"] == "ABORTED"
    assert record["hazard_outcome"] == "COLLISION"
    assert record["rescue_status"] == "RETURNED_NOT_PHYSICAL_SUCCESS"
    assert record["physical_stop_success"] is None
    assert len(safety.requests) == 1
    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]


@pytest.mark.parametrize(
    "condition", [Module2Condition.MANUAL, Module2Condition.NOA_L2]
)
def test_same_viewer_continuation_supports_both_m2_modes(
    tmp_path: Path, condition: Module2Condition
) -> None:
    clock = StrictClock()
    controller, evidence, backend, _, _, _ = controller_with_hazard(
        tmp_path,
        clock,
        [observation(0.0), observation(1.0, collision_actor_id=501)],
        m2_condition=condition,
    )
    advance_to_nominal_m2(controller)

    controller.execute_current_module()

    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["m2_condition"] == condition.value
    assert evidence.viewers[-1].attach_count == 1
    assert len(evidence.viewers[-1].control_modes_at_run) == 1
    assert backend.cleanup_count == 1


def test_observation_error_still_cleans_owned_hazard_actors(tmp_path: Path) -> None:
    clock = StrictClock()
    observations = [observation(0.0), observation(1.0)]
    controller, evidence, backend, _, owned, _ = controller_with_hazard(
        tmp_path, clock, observations, error_after=1
    )
    advance_to_nominal_m2(controller)

    with pytest.raises(OfflineObservationError, match="offline observation failed"):
        controller.execute_current_module()

    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]
    assert evidence.viewers[-1].close_count == 1
    assert evidence.vehicles[-1].destroy_count == 1
    assert controller.session.state is Stage.FINAL_HAZARD
    assert not controller.session.closed
    assert controller._operator_components().runtime_faults
    controller.abort("TEST_OPERATOR_STOP")


def test_safety_owner_failure_is_logged_and_owned_actors_are_cleaned(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    observations = [
        observation(0.0),
        observation(1.0, collision_actor_id=501),
    ]
    controller, _, backend, safety, owned, _ = controller_with_hazard(
        tmp_path,
        clock,
        observations,
        safety_failure=RuntimeError("safe shutdown command failed"),
    )
    advance_to_nominal_m2(controller)

    with pytest.raises(RuntimeError, match="safe shutdown command failed"):
        controller.execute_current_module()

    assert len(safety.requests) == 1
    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]
    with controller.session.services.logger.event_path.open(
        newline="", encoding="utf-8"
    ) as source:
        rows = list(csv.DictReader(source))
    statuses = [
        json.loads(row["event_value"])["status"]
        for row in rows
        if row["event_type"] == "hazard_safe_shutdown"
    ]
    assert statuses == ["REQUESTED", "FAILED"]


def test_cleanup_incomplete_fails_and_records_once(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, _, backend, _, _, _ = controller_with_hazard(
        tmp_path,
        clock,
        [observation(0.0), observation(1.0, collision_actor_id=501)],
        cleanup_evidence=HazardCleanupEvidence(("lead destroy failed",), (101,)),
    )
    advance_to_nominal_m2(controller)

    with pytest.raises(RuntimeError, match="HAZARD_ACTOR_CLEANUP_INCOMPLETE"):
        controller.execute_current_module()

    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["execution_status"] == "FAILED_OFFLINE"
    assert record["cleanup_failures"] == ["lead destroy failed"]
    assert record["remaining_owned_actor_ids"] == [101]
    assert backend.cleanup_count == 1


def test_keyboard_interrupt_records_abort_and_cleans_once(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, _, backend, _, owned, _ = controller_with_hazard(
        tmp_path,
        clock,
        [observation(0.0), observation(1.0)],
        error_after=1,
        observation_failure=KeyboardInterrupt(),
    )
    advance_to_nominal_m2(controller)

    with pytest.raises(KeyboardInterrupt):
        controller.execute_current_module()

    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["execution_status"] == "FAILED_OFFLINE"
    assert record["hazard_state"] == "ABORTED"
    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]


def test_backend_acquisition_failure_cleans_partial_ownership_once(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _, backend, _, owned, _ = controller_with_hazard(
        tmp_path,
        clock,
        [observation(0.0)],
        acquisition_failure=RuntimeError("partial backend acquisition failed"),
    )
    advance_to_nominal_m2(controller)

    with pytest.raises(RuntimeError, match="partial backend acquisition failed"):
        controller.execute_current_module()

    record = controller.session.components.snapshot()["hazard_observation"]
    assert isinstance(record, dict)
    assert record["execution_status"] == "FAILED_OFFLINE"
    assert record["hazard_state"] == "NOT_STARTED"
    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]


def test_initiating_error_survives_final_flush_and_emit_failures(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    initiating_error = OfflineObservationError("initiating observation failure")
    controller, _, backend, _, owned, resources_seen = controller_with_hazard(
        tmp_path,
        clock,
        [observation(0.0), observation(1.0)],
        error_after=1,
        observation_failure=initiating_error,
    )
    advance_to_nominal_m2(controller)
    original_emit = controller.session.services.emit

    def install_finalization_failures() -> None:
        persistence = resources_seen[-1].session.persistence
        assert persistence is not None

        def fail_flush() -> None:
            raise RuntimeError("final hazard flush failed")

        def fail_hazard_emit(name, payload=None, **kwargs) -> None:
            if name == "hazard_observation":
                raise RuntimeError("final hazard emit failed")
            original_emit(name, payload, **kwargs)

        persistence.flush_events = fail_flush
        controller.session.services.emit = fail_hazard_emit

    backend.failure_action = install_finalization_failures

    with pytest.raises(OfflineObservationError) as raised:
        controller.execute_current_module()

    assert raised.value is initiating_error
    assert "final hazard flush failed" in "\n".join(raised.value.__notes__)
    assert "final hazard emit failed" in "\n".join(raised.value.__notes__)
    assert isinstance(
        controller.session.components.snapshot()["hazard_observation"], dict
    )
    assert backend.cleanup_count == 1
    assert [actor.destroy_count for actor in owned] == [1, 1]
