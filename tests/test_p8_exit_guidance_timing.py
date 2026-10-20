"""Pilot launch settings through the real visible-guidance decision lifecycle."""
from dataclasses import replace

import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceCoordinator, ExitAssistanceStatus
from src.p8.operator_cli import _runtime_config, build_parser
from tests.test_p5_exit_assistance import ACTIVE, ReferenceRecorder, coordinator, observation, stamp
from tests.test_p8_signal_exit import payloads, right_signal
from tools import run_pilot


def pilot_runtime():
    settings = run_pilot.load_pilot_settings(run_pilot.PROJECT_ROOT / run_pilot.PILOT_CONFIG)
    return _runtime_config(build_parser().parse_args(run_pilot.build_operator_args(
        settings, participant_id="OFFLINE", run_id="OFFLINE",
    )))


def configured_guidance(module=ExperimentModule.MODULE_1, condition=Module1Condition.NO_SURT):
    runtime = pilot_runtime()
    window = (runtime.module_1_exit_window if module is ExperimentModule.MODULE_1
              else runtime.module_2_exit_window)
    base = coordinator(module, condition, ReferenceRecorder([]))
    calls = []
    subject = ExitAssistanceCoordinator(
        replace(base.config, signal_guided_exit=runtime.module_exit.signal_guided_exit,
                response_timeout_s=runtime.module_exit.response_timeout_s,
                eligibility_open_s=window.open_s, eligibility_close_s=window.close_s),
        ReferenceRecorder([]),
        automatic_route_submission=lambda: calls.append("exit") or True,
        through_route_submission=lambda: calls.append("through") or True,
    )
    subject.start_active_drive(stamp(0))
    return subject, calls, window


@pytest.mark.parametrize("module,condition,opening", [
    (ExperimentModule.MODULE_1, Module1Condition.NO_SURT, 180),
    (ExperimentModule.MODULE_1, Module1Condition.SURT, 180),
    (ExperimentModule.MODULE_2, Module2Condition.MANUAL, 100),
    (ExperimentModule.MODULE_2, Module2Condition.NOA_L2, 100),
])
def test_pilot_waits_for_module_time_and_valid_exit_approach(module, condition, opening):
    subject, calls, window = configured_guidance(module, condition)
    assert window.open_s == opening
    subject.observe(replace(observation(250), timestamp=stamp(opening * 10**9 - 1)))
    assert subject.status is ExitAssistanceStatus.WAITING
    # The minimum elapsed time alone must not display guidance off the approach.
    subject.observe(replace(observation(-1, frame=2), route_matched=False,
                            timestamp=stamp(opening * 10**9)))
    assert subject.presentation is None
    subject.observe(replace(observation(250, frame=3), timestamp=stamp((opening + 1) * 10**9)))
    assert subject.status is ExitAssistanceStatus.DRAFT
    assert calls == []


@pytest.mark.parametrize("offset,expected", [(-1, "CONFIRM"), (0, "NORESPONSE"), (1, "NORESPONSE")])
def test_extended_visible_window_accepts_after_five_seconds_but_not_at_deadline(offset, expected):
    subject, calls, window = configured_guidance()
    t0 = int((window.open_s + 1) * 10**9)
    subject.observe(replace(observation(250), timestamp=stamp(t0)))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(t0))
    subject.update(DriverInput(), ACTIVE, stamp(t0 + 6 * 10**9))
    assert subject.presentation is not None
    assert subject.status is ExitAssistanceStatus.PRESENTED
    assert calls == []
    deadline = t0 + 15 * 10**9
    subject.update(right_signal(), ACTIVE, stamp(deadline + offset))
    decision = payloads(subject, "lc_decision")[0]
    assert decision["decision"] == expected
    assert decision["guidance_deadline_host_monotonic_ns"] == deadline
    if expected == "NORESPONSE":
        subject.update(right_signal(), ACTIVE, stamp(deadline + 10**9))
        assert subject.presentation is None
        assert calls == ["through"]
        assert len(payloads(subject, "lc_decision")) == 1
        assert payloads(subject, "lc_late_indicator_ignored_for_exit")
    else:
        subject.update(DriverInput(), ACTIVE, stamp(deadline + 10**9))
        assert subject.presentation is None
        assert len(payloads(subject, "lc_decision")) == 1
        subject.observe(replace(observation(1100, lane=-4, frame=2),
                                timestamp=stamp(deadline + 2 * 10**9)))
        subject.update(DriverInput(), ACTIVE, stamp(deadline + 2 * 10**9))
        assert calls == ["exit"]  # Timely intent can complete after the text disappears.


def test_extended_window_still_closes_when_vehicle_leaves_the_exit_approach():
    subject, calls, window = configured_guidance()
    t0 = int((window.open_s + 1) * 10**9)
    subject.observe(replace(observation(250), timestamp=stamp(t0)))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(t0))
    subject.observe(replace(observation(-1, frame=2), route_matched=False,
                            timestamp=stamp(t0 + 10**9)))
    assert subject.presentation is None
    assert payloads(subject, "lc_guidance_closed")[0]["presentation_window_status"] == "APPROACH_ENDED"
    assert calls == []
