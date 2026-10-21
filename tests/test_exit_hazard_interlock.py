"""Regression for the completed lane change / stale CONFIRMED pilot failure."""

from dataclasses import replace

import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceStatus
from tests.test_clear_road_hazard import expose, setup
from tests.test_m2_hazard_v1 import tick
from tests.test_p5_exit_assistance import ACTIVE, observation, stamp
from tests.test_p8_signal_exit import CONDITIONS, guided, payloads, right_signal


def observe_at(subject, seconds, frame, *, road=1400, matched=False):
    subject.observe(replace(
        observation(-1 if not matched else 1100, road=road, lane=-4, frame=frame),
        timestamp=stamp(int(seconds * 1e9)), route_matched=matched,
        target_boundary_valid=False,
    ))


def leave_corridor(subject, start=350, frame=2):
    for index in range(5):
        observe_at(subject, start + index * .5, frame + index)


@pytest.mark.parametrize("module,condition", CONDITIONS)
@pytest.mark.parametrize("confirm", [True, False])
def test_abandoned_opportunity_retires_once_without_rewriting_decision(module, condition, confirm):
    subject, calls = guided(module, condition)
    subject.update(right_signal() if confirm else DriverInput(), ACTIVE,
                   stamp(50 if confirm else 6_000_000_030), automatic_lane_change_busy=True)
    decision = "CONFIRM" if confirm else "NORESPONSE"
    leave_corridor(subject)
    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    assert subject._decision == decision and subject._finished
    assert not subject.lane_change_in_progress
    assert payloads(subject, "lc_finished")[0]["termination_reason"] == "EXIT_CORRIDOR_LEFT"
    retired = payloads(subject, "lc_opportunity_retired")[0]
    assert retired["observed_road_id"] == 1400
    assert retired["outcome"] is None  # No fabricated EXIT/MISSED.
    observe_at(subject, 360, 8, road=39, matched=True)
    subject.update(right_signal(), ACTIVE, stamp(361_000_000_000))
    assert calls == [] and len(payloads(subject, "lc_finished")) == 1
    assert len(payloads(subject, "lc_decision")) == 1


@pytest.mark.parametrize("matched", [True, False])
def test_guidance_timeout_or_projection_loss_on_known_approach_does_not_cancel_exit(matched):
    subject, calls = guided(lane=-4)
    subject.update(right_signal(), ACTIVE, stamp(50), automatic_lane_change_busy=True)
    for index in range(10):
        observe_at(subject, 350 + index * .5, 2 + index, road=39, matched=matched)
    assert subject.status is ExitAssistanceStatus.CONFIRMED
    observe_at(subject, 356, 12, road=39, matched=True)
    subject.update(DriverInput(), ACTIVE, stamp(356_000_000_100))
    assert calls == ["exit"]


def test_transient_unknown_and_gapped_observations_do_not_retire():
    subject, _ = guided()
    subject.update(right_signal(), ACTIVE, stamp(50), automatic_lane_change_busy=True)
    observe_at(subject, 350, 2)
    observe_at(subject, 350.5, 3)
    observe_at(subject, 351, 4, road=39, matched=True)
    observe_at(subject, 351.5, 5)
    observe_at(subject, 352, 6)
    observe_at(subject, 380, 7)  # An observation gap is not continuous evidence.
    observe_at(subject, 380.5, 8)
    for index in range(8):
        observe_at(subject, 381 + index * .5, 9 + index, road=None)
    assert subject.status is ExitAssistanceStatus.CONFIRMED
    leave_corridor(subject, start=390, frame=20)
    assert subject.status is ExitAssistanceStatus.INTERRUPTED


def test_retirement_log_error_is_nonfatal_and_does_not_reopen_exit():
    def failed_sink(event):
        raise OSError("disk full")
    subject, _ = guided(event_sink=failed_sink)
    subject.update(right_signal(), ACTIVE, stamp(50), automatic_lane_change_busy=True)
    leave_corridor(subject)
    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    assert subject.logging_errors


def test_distinct_frames_with_quantized_host_clock_still_retire():
    subject, _ = guided()
    subject.update(right_signal(), ACTIVE, stamp(50), automatic_lane_change_busy=True)
    for index in range(10):
        observe_at(subject, 350 + (index // 2) * .5, 2 + index)
    assert subject.status is ExitAssistanceStatus.INTERRUPTED


def test_recorded_late_hazard_schedule_survives_abandoned_exit(tmp_path):
    subject, calls = guided(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)
    # Use a fresh guidance origin to mirror the pilot's 179.172s presentation.
    subject._t0 = stamp(179_172_000_000)
    subject.update(right_signal(), ACTIVE, stamp(183_406_000_000), automatic_lane_change_busy=True)
    observe_at(subject, 184.594, 2, road=39, matched=True)
    assert subject.status is ExitAssistanceStatus.CONFIRMED
    leave_corridor(subject, start=356.937, frame=3)
    scene, world, _, events, _, _ = setup(tmp_path)
    scene.session.exit_assistance = subject
    scene.planned = 598.7594013499878
    scene.next_attempt = scene.planned - scene.config.preparation_s
    expose(scene, world)
    assert scene.claimed and scene.first_display
    assert calls == []
    assert not any(kind == "m2_hazard_preparation_deferred" and
                   value["reason"].startswith("P5") for kind, value in events)
    scene.finalize("M2_END")


def test_actual_lane_change_keeps_blocking_after_exit_retirement_then_rechecks_stability(tmp_path):
    subject, _ = guided(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)
    subject.update(right_signal(), ACTIVE, stamp(50), automatic_lane_change_busy=True)
    scene, world, _, _, _, _ = setup(tmp_path)
    scene.session.exit_assistance = subject
    tick(scene, world, scene.planned - 6)
    assert scene.last_readiness["checks"]["exit_conflict_free"]["status"] == "FAIL"
    assert scene.last_readiness["checks"]["moving_speed_eligible"]["status"] == "NOT_CHECKED"
    leave_corridor(subject)
    tm = scene.session.bundle.traffic_manager_backend
    tm.lane_change_in_flight = "ordinary driver lane change"
    for _ in range(70):
        tick(scene, world, .1)
    assert scene.first_display is None
    assert scene.last_reason == "P5_OR_GENERAL_LANE_CHANGE_IN_FLIGHT"
    tm.lane_change_in_flight = None
    for _ in range(10):
        tick(scene, world, .1)
    assert scene.first_display is None
    for _ in range(35):
        tick(scene, world, .1)
    assert scene.first_display and scene.scene_failure is None
    assert world.vehicle.applied_controls == []
    scene.finalize("M2_END")


@pytest.mark.parametrize("failure", [False, RuntimeError("RPC"), TypeError("unexpected cancellation failure")])
def test_unverified_exit_route_cancellation_blocks_hazard_but_keeps_driving(tmp_path, failure):
    subject, _ = guided(ExperimentModule.MODULE_2, Module2Condition.NOA_L2, lane=-4)
    subject.update(right_signal(), ACTIVE, stamp(50))
    def fail():
        if isinstance(failure, Exception):
            raise failure
        return failure
    subject._automatic_route_cancellation = fail
    leave_corridor(subject)
    scene, world, clock, _, _, _ = setup(tmp_path)
    scene.session.exit_assistance = subject
    tick(scene, world, scene.planned - 6)
    assert scene.last_reason == "P5_EXIT_ROUTE_CANCELLATION_UNVERIFIED"
    tick(scene, world, 601 - clock())
    assert scene.state == "SCHEDULED" and scene.scene_failure is None
    tick(scene, world, 780 - clock())
    assert scene.should_extend_drive(clock(), 780)
    scene.finalize("USER_EXIT")
    assert world.vehicle.is_alive and world.vehicle.applied_controls == []
