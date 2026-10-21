"""The participant must experience this M2's exit opportunity before exposure."""

from dataclasses import replace

import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceStatus
from src.experiment.exit_assistance_types import ExitContext
from src.p8.exit_before_hazard import POLICY, sequence_evidence
from tests.test_clear_road_hazard import setup
from tests.test_exit_hazard_interlock import leave_corridor
from tests.test_m2_hazard_v1 import tick
from tests.test_p5_exit_assistance import ACTIVE, MANUAL, observation, stamp
from tests.test_p8_signal_exit import guided, right_signal


def m2_exit(mode="NOA_ACTIVE", event_sink=None):
    subject, calls = guided(
        ExperimentModule.MODULE_2,
        Module2Condition.MANUAL if mode == "MANUAL" else Module2Condition.NOA_L2,
        mode=MANUAL if mode == "MANUAL" else ACTIVE, event_sink=event_sink,
    )
    subject._t0 = stamp(100_000_000_000)
    return subject, calls


def resolve(subject, outcome, seconds):
    if outcome == "RETIRED":
        leave_corridor(subject, start=seconds - 2, frame=2)
    else:
        obs = observation(1400, lane=2, road=33, frame=2)
        if outcome == "MISSED":
            obs = replace(obs, road_id=1184, lane_id=-4,
                          route_reference_distance_m=10,
                          route_reference_half_width_m=2)
        subject.observe(replace(obs, timestamp=stamp(int(seconds * 1e9))))


def advance(scene, world, seconds):
    for _ in range(round(seconds * 10)):
        tick(scene, world, .1)


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize("case", [
    "missing", "waiting", "draft", "not_presented", "interrupted_without_presentation",
    "guidance_only", "decision_only", "cancelled", "previous_module", "training",
])
def test_unexperienced_or_unresolved_exit_cannot_spawn_hazard_and_drive_continues(tmp_path, mode, case):
    scene, world, clock, events, records, _ = setup(tmp_path, mode)
    subject, _ = m2_exit(mode)
    if case in ("waiting", "draft", "not_presented", "interrupted_without_presentation"):
        subject._t0 = None
        subject.status = {
            "waiting": ExitAssistanceStatus.WAITING,
            "draft": ExitAssistanceStatus.DRAFT,
            "not_presented": ExitAssistanceStatus.NOT_PRESENTED,
            "interrupted_without_presentation": ExitAssistanceStatus.INTERRUPTED,
        }[case]
    elif case == "decision_only":
        subject.update(right_signal(), ACTIVE, stamp(101_000_000_000),
                       automatic_lane_change_busy=True)
    elif case == "cancelled":
        subject._interrupt(stamp(102_000_000_000), "MODE_CHANGED_AFTER_T0")
    elif case in ("previous_module", "training"):
        resolve(subject, "EXIT", 200)
        subject.config = replace(
            subject.config,
            **({"module": ExperimentModule.MODULE_1, "condition": Module1Condition.NO_SURT}
               if case == "previous_module" else {"context": ExitContext.TRAINING}),
        )
    scene.session.exit_assistance = None if case == "missing" else subject
    tick(scene, world, scene.planned - 6)
    advance(scene, world, 10)
    assert scene.first_display is None and scene.backend.cargo is None
    assert not scene.claimed and scene.scene_failure is None
    check = scene.last_readiness["checks"]["exit_before_hazard"]
    assert check["status"] == "FAIL" and check["reason"].startswith("P5_M2_EXIT")
    assert scene.last_readiness["checks"]["moving_speed_eligible"]["status"] == "NOT_CHECKED"
    tick(scene, world, 601 - clock())
    assert scene.state == "SCHEDULED"
    tick(scene, world, 780 - clock())
    assert scene.should_extend_drive(clock(), 780)
    scene.finalize("USER_EXIT")
    assert records[-1]["occurrence_result"] == "NOT_OCCURRED"
    assert records[-1]["event_order_policy"] == POLICY
    assert records[-1]["exit_sequence_at_reveal"] is None
    assert records[-1]["last_readiness"]["checks"]["exit_before_hazard"]["status"] == "FAIL"
    assert world.vehicle.is_alive and world.vehicle.applied_controls == []
    assert any(kind == "m2_hazard_m2_extension_started" for kind, _ in events)


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize("outcome", ["EXIT", "MISSED", "RETIRED"])
def test_late_exit_resolves_before_hazard_then_all_existing_readiness_checks_pass(tmp_path, mode, outcome):
    scene, world, clock, events, records, _ = setup(tmp_path, mode)
    subject, calls = m2_exit(mode)
    scene.session.exit_assistance = subject
    # Move the fixed planned hazard time before the exit completes.
    scene.planned, scene.next_attempt = 400, 394
    tick(scene, world, 394)
    advance(scene, world, 9)
    assert scene.backend.cargo is None and scene.first_display is None
    if outcome != "EXIT":
        subject.update(DriverInput(), ACTIVE if mode == "NOA_ACTIVE" else MANUAL,
                       stamp(402_000_000_000))
        if mode == "NOA_ACTIVE":
            assert subject._decision == "NORESPONSE"
    resolve(subject, outcome, clock())
    ended = subject._opportunity_end_timestamp.host.monotonic_ns
    advance(scene, world, 1)
    assert scene.first_display is None  # Speed/clear-road hold is still required.
    advance(scene, world, 4)
    assert scene.first_display and scene.scene_failure is None
    assert scene.first_geometry["observation_monotonic_ns"] > ended
    assert scene.last_readiness["checks"]["exit_before_hazard"]["status"] == "PASS"
    assert all(check["status"] == "PASS" for check in scene.last_readiness["checks"].values())
    scene.finalize("M2_END")
    proof = records[-1]["exit_sequence_at_reveal"]
    assert proof["resolved_host_monotonic_ns"] == ended
    assert proof["presented_host_monotonic_ns"] < ended
    assert proof["physical_outcome"] == (None if outcome == "RETIRED" else outcome)
    assert proof["resolution_reason"] == ("EXIT_CORRIDOR_LEFT" if outcome == "RETIRED" else "lc_completed")
    assert world.vehicle.applied_controls == world.vehicle.transform_commands == []
    # Only the pre-existing no-response THROUGH policy can submit a branch.
    assert calls == (["through", "cancel"] if mode == "NOA_ACTIVE" and outcome != "EXIT" else [])
    assert scene.session.bundle.automation_runtime.state.control_mode.value == mode
    assert any(kind == "m2_hazard_preparation_deferred" for kind, _ in events)
    end_event = next(value for kind, value in events if kind == "m2_hazard_end")
    assert end_event["exit_sequence_at_reveal"] == proof


def test_noresponse_and_late_signal_do_not_unlock_until_passed_exit(tmp_path):
    scene, world, _, _, _, _ = setup(tmp_path)
    subject, calls = m2_exit()
    scene.session.exit_assistance = subject
    subject.update(DriverInput(), ACTIVE, stamp(106_000_000_000))
    subject.update(right_signal(), ACTIVE, stamp(107_000_000_000))
    assert subject._decision == "NORESPONSE" and calls == ["through"]
    tick(scene, world, scene.planned - 6)
    advance(scene, world, 8)
    assert scene.backend.cargo is None
    assert sequence_evidence(subject)["reason"] == "P5_M2_EXIT_NOT_PHYSICALLY_RESOLVED"
    scene.finalize("M2_END")


def test_sequence_proof_survives_exit_event_sink_and_diagnostic_failures(tmp_path, monkeypatch):
    def failed_sink(event):
        raise OSError("disk full")
    scene, world, _, events, records, _ = setup(tmp_path)
    subject, _ = m2_exit(event_sink=failed_sink)
    resolve(subject, "MISSED", 200)
    assert subject.logging_errors
    scene.session.exit_assistance = subject
    original = scene.services.emit
    def fail_diagnostics(kind, payload):
        if kind in ("m2_hazard_readiness_evaluated", "m2_hazard_preparation_deferred"):
            raise OSError("diagnostic write failed")
        original(kind, payload)
    monkeypatch.setattr(scene.services, "emit", fail_diagnostics)
    tick(scene, world, scene.planned - 6)
    advance(scene, world, 10)
    assert scene.first_display and scene.scene_failure is None
    scene.finalize("M2_END")
    assert records[-1]["exit_sequence_at_reveal"]["status"] == "PASS"
    assert records[-1]["nonfatal_recording_errors"]
    assert world.vehicle.applied_controls == []


def test_invalid_exit_timestamps_never_unlock_hazard(tmp_path):
    scene, world, _, _, _, _ = setup(tmp_path)
    subject = scene.session.exit_assistance
    subject._opportunity_end_timestamp = stamp(1)
    tick(scene, world, scene.planned - 6)
    assert scene.last_reason == "P5_M2_EXIT_SEQUENCE_TIMESTAMP_INVALID"
    assert scene.backend.cargo is None
    scene.finalize("M2_END")
