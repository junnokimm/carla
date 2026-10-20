from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import carla
import pygame
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceCoordinator, ExitAssistanceStatus
from src.experiment.exit_signal import refresh_guidance
from src.experiment.lane_change import LaneChangeDirection
from src.scenario.research_exit_runtime import ResearchExitInteractionObserver, ResearchExitObservationSource
from tests.test_p5_exit_assistance import ACTIVE, MANUAL, ReferenceRecorder, coordinator, observation, stamp
from tests.test_lane_change_execution_lifecycle import make_rig, drive_to_completion
from tests.test_p5_real_driver_view_binding import BindingHarness, make_view, present_frame

CONDITIONS = [
    (ExperimentModule.MODULE_1, Module1Condition.NO_SURT),
    (ExperimentModule.MODULE_1, Module1Condition.SURT),
    (ExperimentModule.MODULE_2, Module2Condition.NOA_L2),
    (ExperimentModule.MODULE_2, Module2Condition.MANUAL),
]


def guided(module=ExperimentModule.MODULE_1, condition=Module1Condition.NO_SURT,
           *, lane=-3, mode=ACTIVE, event_sink=None):
    base = coordinator(module, condition, ReferenceRecorder([]))
    calls = []
    subject = ExitAssistanceCoordinator(
        replace(base.config, signal_guided_exit=True), ReferenceRecorder([]), event_sink,
        automatic_route_submission=lambda: calls.append("exit") or True,
        through_route_submission=lambda: calls.append("through") or True,
        automatic_route_cancellation=lambda: calls.append("cancel") or True,
    )
    subject.observe(replace(observation(250, lane=lane), right_lane_id=lane - 1))
    subject.mark_presented(subject.prepare_presentation(mode), mode, stamp(30))
    return subject, calls


def right_signal():
    return DriverInput(lane_change_direction=LaneChangeDirection.RIGHT, exit_confirm_requested=True)


def payloads(subject, kind):
    return [json.loads(e.payload_json) for e in subject.events if e.event_type == kind]


@pytest.mark.parametrize("module,condition", CONDITIONS)
@pytest.mark.parametrize("lane", [-1, -2, -3, -4])
def test_no_input_never_starts_an_exit(module, condition, lane):
    subject, calls = guided(module, condition, lane=lane)
    subject.update(DriverInput(), ACTIVE, stamp(100))
    assert calls == []
    assert payloads(subject, "lc_decision") == []
    assert subject.status is ExitAssistanceStatus.PRESENTED


@pytest.mark.parametrize("module,condition", CONDITIONS)
def test_signal_during_guidance_then_outer_lane_and_verified_exit(module, condition):
    subject, calls = guided(module, condition, lane=-2)
    subject.update(right_signal(), ACTIVE, stamp(50), stamp(60), automatic_lane_change_busy=True)
    assert calls == []  # Never a synthetic lane-change request.
    decision = payloads(subject, "lc_decision")[0]
    assert decision["decision"] == "CONFIRM"
    assert decision["decision_source"] == "DRIVER"
    assert decision["input_receipt_host_monotonic_ns"] == 50
    assert decision["guidance_deadline_host_monotonic_ns"] == 5_000_000_030
    assert decision["consent_outcome"] is None
    # The signal is on time; completing the maneuver need not fit inside 5s.
    subject.update(DriverInput(), ACTIVE, stamp(6_000_000_030))
    assert subject.presentation is None
    assert len(payloads(subject, "lc_decision")) == 1
    subject.observe(observation(1100, lane=-4, lateral=0, frame=2))
    subject.update(DriverInput(), ACTIVE, stamp(7_000_000_030))
    subject.update(DriverInput(), ACTIVE, stamp(7_000_000_031))
    assert calls == ["exit"]
    subject.observe(observation(1400, lane=2, road=33, frame=3))
    assert subject.status is ExitAssistanceStatus.COMPLETED
    assert calls == ["exit", "cancel"]
    assert payloads(subject, "lc_completed")[0]["consent_outcome"] == "SIGNALLED_AND_EXITED"


@pytest.mark.parametrize("offset,expected", [(-1, "CONFIRM"), (0, "NORESPONSE"), (1, "NORESPONSE")])
def test_signal_deadline_is_same_as_display_deadline(offset, expected):
    subject, calls = guided()
    subject.update(right_signal(), ACTIVE, stamp(5_000_000_030 + offset))
    assert payloads(subject, "lc_decision")[0]["decision"] == expected
    if expected == "NORESPONSE":
        assert subject.presentation is None
        assert calls == ["through"]
        subject.observe(observation(260, frame=2))
        subject.update(right_signal(), ACTIVE, stamp(6_000_000_030))
        assert subject.presentation is None
        assert len(payloads(subject, "lc_decision")) == 1
        assert "exit" not in calls


def test_noresponse_late_signal_uses_general_lane_change_and_mainline():
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    subject, calls = guided()
    subject.update(DriverInput(), ACTIVE, stamp(5_000_000_030))
    rig.persistence.source.host_ns = 6_000_000_030
    observer = ResearchExitInteractionObserver(rig.observer, subject, None, rig.persistence)
    observer.update(right_signal())  # Even stale view input cannot revive consent.
    assert rig.traffic_manager.calls.count(("force_lane_change", True)) == 1
    assert calls == ["through"]
    assert payloads(subject, "lc_decision")[0]["decision"] == "NORESPONSE"
    assert payloads(subject, "lc_late_indicator_ignored_for_exit")
    drive_to_completion(rig, LaneChangeDirection.RIGHT)
    assert observer.state == ACTIVE
    subject.observe(replace(observation(1250, road=1184, lane=-4, frame=2),
                            route_reference_distance_m=10, route_reference_half_width_m=1.75))
    assert payloads(subject, "lc_completed")[0]["outcome"] == "MISSED"


@pytest.mark.parametrize("driver", [DriverInput(brake=.05), DriverInput(steering_engaged=True), DriverInput(deactivation_requested=True)])
def test_manual_override_cancels_exit_and_never_replays(driver):
    subject, calls = guided(lane=-4)
    subject.update(right_signal(), ACTIVE, stamp(50))
    subject.update(driver, MANUAL, stamp(60))
    subject.update(right_signal(), ACTIVE, stamp(70))
    assert calls == ["exit", "cancel"]
    assert subject.status is ExitAssistanceStatus.INTERRUPTED


def test_manual_guidance_never_submits_route():
    subject, calls = guided(mode=MANUAL)
    subject.update(right_signal(), MANUAL, stamp(50))
    subject.update(DriverInput(), MANUAL, stamp(6_000_000_030))
    assert calls == []
    assert payloads(subject, "lc_decision") == []


@pytest.mark.parametrize("choice", ["exit", "through"])
@pytest.mark.parametrize("failure", [False, RuntimeError("RPC"), OSError("disk")])
def test_branch_failure_is_nonfatal_and_not_repeated(choice, failure):
    subject, calls = guided(lane=-4)
    def fail():
        calls.append("failed")
        if isinstance(failure, Exception):
            raise failure
        return failure
    if choice == "exit":
        subject._automatic_route_submission = fail
        subject.update(right_signal(), ACTIVE, stamp(50))
    else:
        subject._through_route_submission = fail
        subject.update(DriverInput(), ACTIVE, stamp(6_000_000_030))
    subject.update(DriverInput(), ACTIVE, stamp(7_000_000_030))
    assert calls.count("failed") == 1
    assert len(payloads(subject, "lc_route_submission_unverified")) == 1


def test_new_log_failure_does_not_stop_guidance_or_route():
    subject, calls = guided(lane=-4, event_sink=lambda event: (_ for _ in ()).throw(OSError("disk")))
    subject.update(right_signal(), ACTIVE, stamp(50))
    assert calls == ["exit"]
    assert subject.logging_errors


def signal_harness():
    harness = BindingHarness(ACTIVE)
    harness.coordinator.config = replace(harness.coordinator.config, signal_guided_exit=True)
    harness.binding = replace(harness.binding, config=replace(
        harness.binding.config, signal_guided_exit=True, indicator_confirmation=True, confirm_key="x"))
    return harness


def test_no_c_r_response_inputs_and_signal_requires_a_visible_flip(monkeypatch):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    harness = signal_harness()
    viewer = make_view(harness.binding)
    def collect(key):
        return viewer._collect_driver_input([pygame.event.Event(pygame.KEYDOWN, key=key)])
    assert not collect(pygame.K_x).exit_confirm_requested
    viewer._collect_driver_input([pygame.event.Event(pygame.KEYUP, key=pygame.K_x)])
    present_frame(viewer)
    for key in (pygame.K_c, pygame.K_r):
        driver = collect(key)
        assert not driver.exit_confirm_requested and not driver.exit_reject_requested
    assert collect(pygame.K_x).exit_confirm_requested


def test_expired_guidance_disappears_and_never_reappears(monkeypatch):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    harness = signal_harness()
    viewer = make_view(harness.binding)
    present_frame(viewer)
    harness.persistence.source._next_value = 6_000_000_030
    present_frame(viewer)
    assert viewer._exit_view._prepared is None
    assert not viewer._exit_view.signal_guidance_visible
    assert payloads(harness.coordinator, "lc_guidance_hidden")
    harness.coordinator.observe(observation(270, frame=2))
    present_frame(viewer)
    assert viewer._exit_view._prepared is None
    assert len(payloads(harness.coordinator, "lc_decision")) == 1


@pytest.mark.parametrize("device", ["keyboard", "wheel"])
@pytest.mark.parametrize("direction", list(LaneChangeDirection))
@pytest.mark.parametrize("visible", [True, False])
def test_real_blinker_path_keeps_one_change_and_auto_off(monkeypatch, device, direction, visible):
    from src.scenario.research_noa_view import ResearchLiveDriverView, ResearchDriverViewConfig
    from tests.test_driver_view import FakeWorld, FakeSensor
    from src.scenario.driver_view import CameraFeed
    from tests.test_p8_tsxw_ordering import sample as wheel_sample
    rig = make_rig(direction, submit_general=False)
    rig.vehicle.current_light_state = carla.VehicleLightState.LowBeam
    harness = signal_harness()
    subject = harness.coordinator
    observer = ResearchExitInteractionObserver(rig.observer, subject, None, rig.persistence)
    harness.binding = replace(harness.binding, observer=observer)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    view = ResearchLiveDriverView(FakeWorld(), rig.vehicle, ResearchDriverViewConfig(automation_state_source=lambda: observer.state))
    view._feeds = [CameraFeed("front", FakeSensor())]
    view.set_exit_view_binding(harness.binding)
    if visible:
        present_frame(view)
    right = direction is LaneChangeDirection.RIGHT
    view._tsxw_input = SimpleNamespace(poll=lambda _: wheel_sample(
        left_pressed=device == "wheel" and not right, left_held=device == "wheel" and not right,
        right_pressed=device == "wheel" and right, right_held=device == "wheel" and right,
    ))
    events = [] if device == "wheel" else [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x if right else pygame.K_z, repeat=0)]
    driver = view._collect_driver_input(events)
    assert driver.lane_change_direction is direction
    assert driver.exit_confirm_requested == (visible and right)
    observer.update(driver)
    assert rig.traffic_manager.calls.count(("force_lane_change", right)) == 1
    assert bool(payloads(subject, "lc_decision")) == (visible and right)
    drive_to_completion(rig, direction)
    assert observer.state == ACTIVE
    assert rig.vehicle.current_light_state == carla.VehicleLightState.LowBeam


@pytest.mark.parametrize("choice", ["Right", "Straight"])
def test_backend_branch_clears_on_manual_without_changing_activation(monkeypatch, choice):
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    calls = []
    monkeypatch.setattr(rig.traffic_manager, "set_route", lambda actor, path, empty: calls.append((path, empty)), raising=False)
    submit = rig.backend.submit_automatic_exit_route if choice == "Right" else rig.backend.submit_exit_through_route
    assert submit()
    rig.backend.enter_manual_control()
    rig.backend.enter_noa_control()
    assert calls == [([choice], True), ([], True)]
    assert rig.backend.active


def test_branch_cancel_error_never_blocks_manual(monkeypatch):
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    monkeypatch.setattr(rig.traffic_manager, "set_route", lambda *args: None, raising=False)
    assert rig.backend.submit_automatic_exit_route()
    monkeypatch.setattr(rig.traffic_manager, "set_route", lambda *args: (_ for _ in ()).throw(RuntimeError("RPC")))
    rig.backend.enter_manual_control()
    assert not rig.backend.active
    with pytest.raises(RuntimeError, match="cancellation unverified"):
        rig.backend.enter_noa_control()


@pytest.mark.parametrize("module", list(ExperimentModule))
def test_actual_runner_wires_signal_policy_without_autonomous_steps(tmp_path, module):
    from src.scenario.research_noa_runtime import ResearchNoARunner
    from tests.test_research_noa_operator_p5 import operator_p5_nominal_config
    from tests.research_noa_fakes import FakeClient, FakeResearchWorld, FakeResearchVehicle, FakeDriverViewFactory, FakeTrafficManagerRuntimeAttestor
    config = operator_p5_nominal_config(tmp_path, module)
    config = replace(
        config, traffic_manager_port=8000, activation_speed_hold=False, speed_policy=None,
        control_config=replace(config.control_config, longitudinal=replace(config.control_config.longitudinal, target_speed_kmh=None)),
        exit_assistance=replace(config.exit_assistance, signal_guided_exit=True, indicator_confirmation=True, confirm_key="x"),
    )
    world = FakeResearchWorld(FakeResearchVehicle())
    world.map.name = "Carla/Maps/Town04"
    runner = ResearchNoARunner(config, client=FakeClient(world), viewer_factory=FakeDriverViewFactory(),
                              exit_route_validator=lambda *args: None, tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor())
    with runner.session() as session:
        subject = session.exit_assistance
        assert subject.config.signal_guided_exit
        assert subject._assisted_submission is None
        assert subject._automatic_route_submission.__name__ == "submit_automatic_exit_route"
        assert subject._through_route_submission.__name__ == "submit_exit_through_route"


@pytest.mark.parametrize("decision,outcome,classification", [
    ("CONFIRM", "EXIT", "EXIT_CONFIRMED"), ("NORESPONSE", "MISSED", "NORMAL_MISSED"),
    ("NORESPONSE", "EXIT", "INCOMPLETE"), ("CONFIRM", "MISSED", "NORMAL_MISSED"),
])
def test_saved_result_keeps_intent_and_physical_outcome_separate(tmp_path, decision, outcome, classification):
    from tests.test_p8_operator_p5_results import _write_events, _config, _live
    from src.p8.operator_exit_result import configured_exit_result
    config = replace(_config(tmp_path), signal_guided_exit=True, indicator_confirmation=True, confirm_key="x")
    identity = {"lc_event_id": config.lc_event_id}
    path = tmp_path / "events.csv"
    _write_events(path, (
        ("p5_exit_manifest", 10, identity),
        ("lc_presented", 20, {**identity, "t0_host_monotonic_ns": 20}),
        ("lc_decision", 30, {**identity, "decision": decision}),
        ("lc_completed", 60, {**identity, "outcome": outcome}),
    ))
    result = configured_exit_result(path, config, live=_live(path))
    assert result.classification.value == classification
    assert result.decision == decision
    assert result.physical_outcome == outcome
