import csv
from dataclasses import replace

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import Module2Condition
from src.p7.display import HazardFrameVisibility
from src.p7.policy import OutcomeDecision
from src.p7.types import HazardOutcome, P7Error
from src.p8.model import Stage
from src.p8.operator_hazard import OperatorHazardLoop
from src.scenario.camera_feed import DisplayedCameraFrame
from tests.p8_hazard_execution_support import (
    StrictClock,
    advance_to_nominal_m2,
    controller_with_hazard,
    observation,
)
from tests.test_p7_policy_boundaries import FixturePolicy


def events(controller):
    return list(
        csv.DictReader(
            controller.session.services.logger.event_path.open(encoding="utf-8")
        )
    )


def test_p8_780_boundary_uses_injected_rule_and_retains_actual_manual_after_override(
    tmp_path,
):
    controller, evidence, backend, _, owned, resources = controller_with_hazard(
        tmp_path,
        StrictClock(),
        [
            observation(0),
            observation(26),
            observation(24),
            observation(26),
            observation(27, lateral=1, reveal=True),
            observation(28, lateral=1, reveal=True),
            observation(29, lateral=1, reveal=True),
        ],
        m2_condition=Module2Condition.NOA_L2,
    )
    policy = FixturePolicy(OutcomeDecision(HazardOutcome.STOPPED, "TEST_STOPPED"))
    plan = controller.runtime.hazard_execution
    runtime = replace(
        controller.runtime,
        hazard_execution=replace(plan, approval=replace(plan.approval, policy=policy)),
    )
    controller.runtime = controller.session.components.runtime = runtime
    advance_to_nominal_m2(controller)
    evidence.driver_inputs[0] = DriverInput(deactivation_requested=True)
    controller.execute_current_module()
    record = controller.session.components.snapshot()["hazard_observation"]
    assert record["hazard_outcome"] == "STOPPED"
    assert record["auto_state"] == record["auto_state_at_hazard_end"] == "MANUAL"
    assert record["m2_condition"] == "NOA_L2"
    assert record["hazard_visible_t"] is None
    assert record["trigger_policy"] == "INJECTED_POLICY"
    assert backend.actors.reveal_count == 1
    assert [a.destroy_count for a in owned] == [1, 1]
    assert resources[0].identities.shutdown_brake_status == "NOT_ATTEMPTED"
    assert evidence.viewers[-1].attach_count == 1
    result = next(
        r
        for r in controller.session.components.runs
        if r.stage is Stage.MODULE_2_RUNNING
    )
    assert result.actual_active_drive_s == 780


def test_stale_backend_geometry_is_rejected_without_restamping_and_reservation_stays(
    tmp_path,
):
    controller, _, backend, _, owned, _ = controller_with_hazard(
        tmp_path,
        StrictClock(),
        [observation(0), observation(22)],
    )
    original = backend.observe_geometry
    prior = []

    def observe(sample):
        result = original(sample)
        if prior:
            return replace(result, timestamp=prior[0])
        prior.append(result.timestamp)
        return result

    backend.observe_geometry = observe
    advance_to_nominal_m2(controller)
    with pytest.raises(P7Error, match="GEOMETRY_SAMPLE_MISMATCH"):
        controller.execute_current_module()
    record = controller.session.components.snapshot()["hazard_observation"]
    assert record["last_observation_host_monotonic_ns"] == prior[0].host.monotonic_ns
    assert record["occurrence_status"] == "RESERVED_NO_AUTOMATIC_RETRY"
    assert backend.actors.reveal_count == 0
    assert [a.destroy_count for a in owned] == [1, 1]


@pytest.mark.parametrize("samples", [[], [observation(0)]])
def test_stream_end_is_not_normal_hazard_completion(tmp_path, samples):
    controller, _, backend, _, _, _ = controller_with_hazard(
        tmp_path, StrictClock(), samples
    )
    advance_to_nominal_m2(controller)
    controller.execute_current_module()
    record = controller.session.components.snapshot()["hazard_observation"]
    assert record["execution_status"] == "ABORTED_OFFLINE"
    assert record["terminal_reason"] == "OBSERVATION_STREAM_ENDED"
    ends = [
        r["event_value"]
        for r in events(controller)
        if r["event_type"] == "stage_end" and r["phase"] == "FINAL_HAZARD_ASSESSMENT"
    ]
    assert ends == ["FINAL_HAZARD:HAZARD_ABORTED:OBSERVATION_STREAM_ENDED"]
    assert backend.cleanup_count == 1


def test_reentrant_entry_start_and_post_finalize_tick_cannot_spawn_or_claim_again(
    tmp_path,
):
    controller, _, backend, _, _, _ = controller_with_hazard(
        tmp_path,
        StrictClock(),
        [observation(0), observation(1, collision_actor_id=5)],
    )
    factory = controller.runtime.hazard_execution.backend_factory
    acquire = factory.acquire

    def acquiring(resources):
        loop = controller.session.components.hazard_loop
        controller.session.components.enter(Stage.FINAL_HAZARD)
        assert controller.session.components.hazard_loop is loop
        with pytest.raises(P7Error, match="ALREADY_STARTED"):
            loop.start()
        return acquire(resources)

    factory.acquire = acquiring
    advance_to_nominal_m2(controller)
    controller.execute_current_module()
    reads = backend.read_count
    assert (
        controller.session.components.hazard_loop.after_control_applied(DriverInput())
        is False
    )
    assert backend.read_count == reads and backend.actors.prepare_count == 1


@pytest.mark.parametrize("safety_fails", [False, True])
def test_event_write_failure_cannot_prevent_safety_request_or_hide_primary_error(
    tmp_path, safety_fails
):
    controller, _, backend, safety, owned, _ = controller_with_hazard(
        tmp_path,
        StrictClock(),
        [observation(0), observation(1, collision_actor_id=5)],
        safety_failure=RuntimeError("safety failed") if safety_fails else None,
    )
    original = backend.observe_geometry

    def observe(sample):
        result = original(sample)
        if backend.read_count == 2:

            def failed(event):
                raise RuntimeError("primary hazard event write failed")

            controller.session.components.hazard_loop._scenario._sink = failed
        return result

    backend.observe_geometry = observe
    advance_to_nominal_m2(controller)
    with pytest.raises(RuntimeError, match="primary hazard event write failed"):
        controller.execute_current_module()
    assert len(safety.requests) == 1
    assert [a.destroy_count for a in owned] == [1, 1]
    record = controller.session.components.snapshot()["hazard_observation"]
    assert record["physical_stop_success"] is None
    assert record["rescue_status"] == (
        "FAILED" if safety_fails else "RETURNED_NOT_PHYSICAL_SUCCESS"
    )


@pytest.mark.parametrize(
    "case", ["visible", "mismatch", "missing", "unknown", "pretrigger"]
)
def test_p8_logs_frame_matched_display_candidate_without_validating_canonical_onset(
    tmp_path, monkeypatch, case
):
    controller, _, backend, _, _, _ = controller_with_hazard(
        tmp_path,
        StrictClock(),
        [observation(0), observation(22), observation(23, lateral=1, reveal=True)],
    )
    backend.visibility_for_frame = lambda frame: (
        None
        if case == "unknown"
        else HazardFrameVisibility(
            frame.sensor_actor_id,
            frame.frame + int(case == "mismatch"),
            True,
            "TEST_FRAME_DETECTOR",
        )
    )
    original = OperatorHazardLoop.after_control_applied

    def with_fake_display(loop, driver):
        active = original(loop, driver)
        if (
            loop._scenario.metrics.trigger_t is not None or case == "pretrigger"
        ) and active:
            # Fake viewer's display completion; actual DriverView plumbing has a
            # separate test that exercises blit/present/flip and callback races.
            sample = loop._last
            frame = DisplayedCameraFrame(
                123, 456, 7.0, 100.0, sample.timestamp.host.monotonic_ns + 1
            )
            active = (
                loop.after_display_completed(None if case == "missing" else frame)
                and active
            )
        return active

    monkeypatch.setattr(OperatorHazardLoop, "after_control_applied", with_fake_display)
    advance_to_nominal_m2(controller)
    if case == "mismatch":
        with pytest.raises(P7Error, match="DISPLAY_EVIDENCE_MISMATCH"):
            controller.execute_current_module()
    else:
        controller.execute_current_module()
    record = controller.session.components.snapshot()["hazard_observation"]
    assert record["hazard_visible_t"] is None
    if case in ("visible", "pretrigger"):
        assert record["first_displayed_frame"] == 456
        assert record["display_candidate"]["participant_perception"] == "NOT_MEASURED"
        assert (
            sum(
                r["event_type"] == "hazard_display_observation"
                for r in events(controller)
            )
            == 1
        )
    else:
        assert record["first_displayed_frame"] is None
    if case == "pretrigger":
        assert record["terminal_reason"] == "VISIBLE_BEFORE_TRIGGER"
        assert backend.actors.reveal_count == 0
    if case in ("missing", "unknown"):
        assert record["visibility_status"] in (
            "DISPLAY_FRAME_UNOBSERVABLE",
            "FRAME_VISIBILITY_UNOBSERVABLE",
        )
