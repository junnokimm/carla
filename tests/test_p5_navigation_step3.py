import json
from dataclasses import replace

import pygame
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceStatus, ExitTerminationReason
from tests.test_p5_exit_assistance import (
    ACTIVE,
    MANUAL,
    ReferenceRecorder,
    coordinator,
    observation,
    stamp,
)
from tests.test_p5_real_driver_view_binding import (
    BindingHarness,
    make_view,
    present_frame,
)
from tests.test_phase2_p5_timing import coordinator as timed
from tests.test_phase2_p5_timing import observation as timed_observation


@pytest.mark.parametrize("state", [MANUAL, ACTIVE])
def test_navigation_after_confirmation_envelope_never_arms_assistance(state):
    reference = ReferenceRecorder([])
    s = coordinator(ExperimentModule.MODULE_1, Module1Condition.NO_SURT, reference)
    s.config = replace(s.config, route=replace(s.config.route, navigation_end_m=1200))
    s.observe(observation(1100))
    assert s.status is ExitAssistanceStatus.DRAFT
    rendered = s.prepare_presentation(state)
    assert rendered.recommendation is False
    s.mark_presented(rendered, state, stamp(15))
    s.update(DriverInput(exit_confirm_requested=True), state, stamp(20))
    s.update(DriverInput(), state, stamp(6_000_000_000))
    assert not reference.armed
    assert not any(e.event_type == "lc_decision" for e in s.events)


@pytest.mark.parametrize(
    "station,matched",
    [(-1, False), (-1, True), (float("nan"), True), (float("inf"), True)],
)
def test_projection_loss_hides_distance_and_recovers_without_second_t0(
    station, matched
):
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    h.coordinator.observe(replace(observation(station, frame=2), route_matched=matched))
    assert h.binding.prepare_presentation() is None
    assert h.coordinator.status is ExitAssistanceStatus.PRESENTED
    unavailable = [
        json.loads(e.payload_json)
        for e in h.coordinator.events
        if e.event_type == "lc_navigation_unavailable"
    ]
    assert len(unavailable) == 1
    assert unavailable[0]["navigation_distance_to_exit_m"] is None
    assert unavailable[0]["outcome"] is None
    h.coordinator.observe(observation(350, frame=3))
    present_frame(view)
    assert h.coordinator.presentation.distance_to_exit_m == 850
    assert sum(e.event_type == "lc_presented" for e in h.coordinator.events) == 1
    assert (
        sum(e.event_type == "lc_navigation_restored" for e in h.coordinator.events) == 1
    )


@pytest.mark.parametrize("reason", list(ExitTerminationReason))
def test_finish_clears_navigation_and_stale_input_before_next_frame(reason):
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    h.coordinator.finish(reason, stamp(40))
    request = view._exit_view.requests(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    )
    assert request == (False, False)
    h.coordinator.observe(observation(300, frame=2))
    assert h.binding.prepare_presentation() is None


@pytest.mark.parametrize(
    "module,condition,state,recommendation",
    [
        (ExperimentModule.MODULE_1, Module1Condition.NO_SURT, MANUAL, False),
        (ExperimentModule.MODULE_1, Module1Condition.NO_SURT, ACTIVE, True),
        (ExperimentModule.MODULE_2, Module2Condition.MANUAL, MANUAL, False),
        (ExperimentModule.MODULE_2, Module2Condition.NOA_L2, ACTIVE, True),
    ],
)
def test_common_navigation_and_t0_branch(module, condition, state, recommendation):
    s = coordinator(module, condition, ReferenceRecorder([]))
    s.observe(observation(250))
    rendered = s.prepare_presentation(state)
    assert rendered.navigation_text == "0.9 km 앞 출구, 우측 차로로"
    assert rendered.recommendation is recommendation
    s.mark_presented(rendered, state, stamp(15))
    payload = json.loads(s.events[-1].payload_json)
    assert payload["noa_state_t0"] == state.control_mode.value
    assert payload["recommendation_at_t0"] is recommendation
    assert payload["navigation_distance_to_exit_m"] == 950


def test_late_eligibility_records_actual_location_not_one_kilometer():
    h = BindingHarness(MANUAL)
    s = h.coordinator
    s.config = replace(s.config, eligibility_open_s=10, eligibility_close_s=20)
    s.config = replace(s.config, route=replace(s.config.route, navigation_start_m=200))
    s.start_active_drive(stamp(0))
    s.observe(replace(observation(600, frame=2), timestamp=stamp(10_000_000_000)))
    s.mark_presented(s.prepare_presentation(MANUAL), MANUAL, stamp(10_000_000_010))
    payload = json.loads(s.events[-1].payload_json)
    assert payload["navigation_distance_to_exit_m"] == 600
    assert payload["navigation_start_offset_m"] == 400


def test_expired_recommendation_cannot_take_stale_key_before_redraw():
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    h.coordinator.observe(observation(1100, frame=2))
    assert not h.binding.prepare_presentation().recommendation
    assert view._exit_view.requests(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    ) == (False, False)


@pytest.mark.parametrize("decision", ["confirm", "reject", "timeout", "mode_change"])
def test_decision_removes_recommendation_but_retains_valid_basic_navigation(decision):
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    state = MANUAL if decision == "mode_change" else ACTIVE
    h.interaction.state = state
    h.coordinator.update(
        DriverInput(
            exit_confirm_requested=decision == "confirm",
            exit_reject_requested=decision == "reject",
        ),
        state,
        stamp(6_000_000_040 if decision == "timeout" else 40),
    )
    assert h.binding.prepare_presentation() is not None
    assert not h.binding.prepare_presentation().recommendation
    assert view._exit_view.requests(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r)]
    ) == (False, False)


@pytest.mark.parametrize("lane", [-1, -2, -4])
def test_navigation_does_not_expand_approach_lanes(lane):
    s = coordinator(
        ExperimentModule.MODULE_1, Module1Condition.NO_SURT, ReferenceRecorder([])
    )
    s.observe(observation(250, lane=lane))
    assert s.status is ExitAssistanceStatus.WAITING
    assert s.presentation is None


def test_time_window_missed_position_then_next_lap_and_expiry():
    s = timed()
    s.observe(timed_observation(9, 300))
    assert s.status is ExitAssistanceStatus.WAITING
    s.observe(timed_observation(10, 1100))
    assert s.status is ExitAssistanceStatus.WAITING
    s.observe(timed_observation(15, 300))
    assert s.status is ExitAssistanceStatus.DRAFT
    # No draw/flip: neither a draft nor a passing opportunity creates t0.
    s.observe(timed_observation(21, 300))
    assert s.status is ExitAssistanceStatus.NOT_PRESENTED
    assert not any(e.event_type == "lc_presented" for e in s.events)


def test_presentation_loss_blocks_input_until_new_successful_flip():
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    h.coordinator.observe(replace(observation(-1, frame=2), route_matched=False))
    assert view._exit_view.requests(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    ) == (False, False)
    h.coordinator.observe(observation(300, frame=3))
    events = [
        pygame.event.Event(pygame.KEYUP, key=pygame.K_c),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c),
    ]
    assert view._exit_view.requests(events) == (False, False)
    present_frame(view)
    assert view._exit_view.requests(events) == (True, False)


def test_invalid_projection_cannot_complete_previously_observed_entry():
    h = BindingHarness(MANUAL)
    view = make_view(h.binding)
    present_frame(view)
    h.coordinator.observe(observation(1200, road=1191, section=1, lane=-2, frame=2))
    h.coordinator.observe(observation(float("inf"), frame=3))
    assert h.coordinator.status is not ExitAssistanceStatus.COMPLETED
    assert not any(e.event_type == "lc_completed" for e in h.coordinator.events)


def test_stale_post_finish_flip_cannot_reactivate_inputs():
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    view._prepare_frame()
    view._draw(pygame.Surface((1280, 720)))
    h.coordinator.finish(ExitTerminationReason.ERROR, stamp(40))
    view._after_display_flip()
    assert view._exit_view.requests(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    ) == (False, False)


def test_observed_movement_removes_unexecutable_recommendation():
    h = BindingHarness(ACTIVE)
    view = make_view(h.binding)
    present_frame(view)
    h.coordinator.observe(observation(260, lateral=-3.0, frame=2))
    assert h.binding.prepare_presentation() is not None
    assert not h.binding.prepare_presentation().recommendation
    assert view._exit_view.requests(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    ) == (False, False)
