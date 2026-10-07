from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
    ExitTerminationReason,
)
from src.experiment.exit_route import ExitRoute, RoutePoint, project_route
from src.experiment.lane_geometry import PlanarPose
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope


def stamp(value: int) -> TimestampEnvelope:
    return TimestampEnvelope(HostClockTimestamp(value, value + 1000))


def route() -> ExitRoute:
    return ExitRoute(
        route_id="town04-exit-39",
        route_version="town04-exits-dev-v3",
        map_name="Carla/Maps/Town04",
        source_lane_id=-3,
        target_lane_id=-4,
        fork_distance_m=1200.0,
        points=(
            RoutePoint(0.0, 0.0, 0.0, 0.0, 39, 0, -3, 3.5),
            RoutePoint(1000.0, 0.0, 0.0, 1000.0, 39, 0, -3, 3.5),
            RoutePoint(1100.0, 0.0, 0.0, 1100.0, 39, 0, -4, 3.5),
            RoutePoint(1200.0, 0.0, 0.0, 1200.0, 1191, 1, -2, 3.5),
            RoutePoint(1400.0, 0.0, 0.0, 1400.0, 33, 0, 2, 3.5),
        ),
        through_segments=((1184, 0, -4),),
        exit_segments=((1191, 1, -2), (33, 0, 2)),
        initiation_end_m=1000.0,
        completion_distance_m=1400.0,
    )


@dataclass
class ReferenceRecorder:
    armed: list[ExitRoute]
    cancel_count: int = 0

    def arm(self, selected: ExitRoute, point_index: int) -> None:
        self.armed.append(selected)

    def cancel(self) -> None:
        self.cancel_count += 1


def coordinator(
    module: ExperimentModule,
    condition: Module1Condition | Module2Condition,
    reference: ReferenceRecorder,
    *,
    context: ExitContext = ExitContext.MODULE,
) -> ExitAssistanceCoordinator:
    return ExitAssistanceCoordinator(
        ExitAssistanceConfig(
            module=module,
            condition=condition,
            context=context,
            lc_event_id=f"{context.value.lower()}-exit-1",
            route=route(),
            navigation_trigger_distance_m=1000.0,
            response_timeout_s=5.0,
            lateral_onset_threshold_m=0.15,
        ),
        reference,
    )


ACTIVE = AutomationState(
    AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
)
MANUAL = AutomationState(AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL)


def observation(
    distance: float,
    *,
    lateral: float = -3.5,
    road: int = 39,
    section: int = 0,
    lane: int = -3,
    frame: int = 1,
) -> ExitObservation:
    return ExitObservation(
        timestamp=stamp(frame * 10),
        route_distance_m=distance,
        lateral_offset_m=lateral,
        road_id=road,
        section_id=section,
        lane_id=lane,
        carla_frame=frame,
        carla_simulation_seconds=frame / 10.0,
        target_boundary_valid=True,
    )


def test_route_projection_uses_ordered_polyline_distance_not_euclidean() -> None:
    selected = ExitRoute(
        route_id="curve",
        route_version="v1",
        map_name="Town04",
        source_lane_id=-3,
        target_lane_id=-4,
        fork_distance_m=20.0,
        points=(
            RoutePoint(0.0, 0.0, 0.0, 0.0, 1, 0, -3, 3.5),
            RoutePoint(10.0, 0.0, 0.0, 10.0, 1, 0, -4, 3.5),
            RoutePoint(10.0, 10.0, 1.5707963268, 20.0, 1, 0, -4, 3.5),
        ),
        through_segments=(),
        exit_segments=((1, 0, -4),),
    )

    projected = project_route(selected, PlanarPose(9.0, 8.0, 1.5707963268))

    assert projected.distance_m == pytest.approx(18.0)
    assert projected.lateral_offset_m == pytest.approx(1.0)


def test_requests_before_first_presentation_cannot_confirm_and_t0_freezes_mode() -> (
    None
):
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_1, Module1Condition.SURT, reference)
    subject.observe(observation(250.0))

    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(20))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))

    assert subject.status is ExitAssistanceStatus.PRESENTED
    assert reference.armed == []
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["t0_host_monotonic_ns"] == 30
    assert payload["noa_state_t0"] == "NOA_ACTIVE"


@pytest.mark.parametrize(
    ("module", "condition", "mode", "recommended"),
    [
        (ExperimentModule.MODULE_1, condition, mode, mode is ACTIVE)
        for condition in Module1Condition
        for mode in (MANUAL, ACTIVE)
    ]
    + [
        (ExperimentModule.MODULE_2, Module2Condition.MANUAL, MANUAL, False),
        (ExperimentModule.MODULE_2, Module2Condition.NOA_L2, ACTIVE, True),
        (ExperimentModule.MODULE_2, Module2Condition.NOA_L2, MANUAL, False),
    ],
)
def test_presented_branch_uses_actual_t0_mode_and_module_condition(
    module: ExperimentModule,
    condition: Module1Condition | Module2Condition,
    mode: AutomationState,
    recommended: bool,
) -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(module, condition, reference)
    subject.observe(observation(250.0))

    subject.mark_presented(subject.prepare_presentation(mode), mode, stamp(30))

    assert subject.presentation is not None
    assert subject.presentation.recommendation is recommended


def test_confirm_arms_existing_reference_only_after_fresh_safe_input() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_2, Module2Condition.NOA_L2, reference)
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))

    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(40))
    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(50))

    assert subject.status is ExitAssistanceStatus.CONFIRMED
    assert reference.armed == [route()]
    decisions = [event for event in subject.events if event.event_type == "lc_decision"]
    assert len(decisions) == 1
    assert (
        json.loads(decisions[0].payload_json)["input_receipt_host_monotonic_ns"] == 40
    )


@pytest.mark.parametrize(
    ("driver_input", "expected_status"),
    [
        (DriverInput(exit_reject_requested=True), ExitAssistanceStatus.REJECTED),
        (DriverInput(), ExitAssistanceStatus.NORESPONSE),
        (
            DriverInput(exit_confirm_requested=True, brake=0.2),
            ExitAssistanceStatus.INTERRUPTED,
        ),
        (
            DriverInput(
                exit_confirm_requested=True,
                steering=0.2,
                steering_engaged=True,
            ),
            ExitAssistanceStatus.INTERRUPTED,
        ),
    ],
)
def test_nonconfirm_paths_never_arm_reference(
    driver_input: DriverInput,
    expected_status: ExitAssistanceStatus,
) -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_2, Module2Condition.NOA_L2, reference)
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))

    at = stamp(
        6_000_000_031 if expected_status is ExitAssistanceStatus.NORESPONSE else 40
    )
    subject.update(driver_input, ACTIVE, at)

    assert subject.status is expected_status
    assert reference.armed == []


def test_late_confirm_after_timeout_is_noresponse_without_input_receipt() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_2, Module2Condition.NOA_L2, reference)
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))

    subject.update(
        DriverInput(exit_confirm_requested=True),
        ACTIVE,
        stamp(6_000_000_031),
    )

    assert subject.status is ExitAssistanceStatus.NORESPONSE
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["input_receipt_host_monotonic_ns"] is None
    assert payload["decision_commit_host_monotonic_ns"] == 6_000_000_031
    assert reference.armed == []


def test_duplicate_observation_frame_cannot_create_false_crossing() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_2, Module2Condition.NOA_L2, reference)
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))
    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(40))
    subject.observe(observation(450.0, lateral=3.5, frame=6))
    before = sum(event.event_type == "lc_boundary_crossing" for event in subject.events)

    subject.observe(observation(500.0, lateral=1.0, frame=6))

    after = sum(event.event_type == "lc_boundary_crossing" for event in subject.events)
    assert after == before


def test_manual_branch_observes_change_proxy_without_automatic_assistance() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_2, Module2Condition.MANUAL, reference)
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(30))

    subject.observe(observation(300.0, lateral=0.2, frame=4))

    assert reference.armed == []
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["decision"] == "MANUAL_CHANGE_OBSERVED_PROXY"
    assert payload["decision_commit_host_monotonic_ns"] is None
    assert payload["observed_change_proxy_host_monotonic_ns"] == 40


def test_mode_change_after_t0_cancels_confirmed_route_without_auto_resume() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(
        ExperimentModule.MODULE_1, Module1Condition.NO_SURT, reference
    )
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))
    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(40))

    subject.update(DriverInput(), MANUAL, stamp(50))
    subject.update(DriverInput(), ACTIVE, stamp(60))

    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    assert reference.cancel_count == 1
    assert reference.armed == [route()]


def test_center_crossing_and_exit_outcome_require_real_observations() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_2, Module2Condition.NOA_L2, reference)
    subject.observe(observation(250.0))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(30))
    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(40))

    subject.observe(observation(450.0, lateral=3.5, frame=6))
    subject.observe(observation(500.0, lateral=1.7, frame=7))
    subject.observe(observation(1250.0, road=1191, section=1, lane=-2, frame=8))
    subject.observe(observation(1400.0, road=33, section=0, lane=2, frame=9))

    assert subject.status is ExitAssistanceStatus.COMPLETED
    crossing = next(
        event for event in subject.events if event.event_type == "lc_boundary_crossing"
    )
    payload = json.loads(crossing.payload_json)
    assert payload["tL_host_monotonic_ns"] == 60
    assert payload["observation_frame"] == 6
    assert payload["crossing_criterion"] == "vehicle_center_target_boundary"


def test_finish_distinguishes_not_reached_from_user_exit() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(ExperimentModule.MODULE_1, Module1Condition.SURT, reference)

    subject.finish(ExitTerminationReason.TIME_CAP, stamp(90))

    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["outcome"] == "NOT_REACHED"
    assert payload["termination_reason"] == "TIME_CAP"


def test_training_context_has_distinct_identity_and_resets_state() -> None:
    reference = ReferenceRecorder([])
    subject = coordinator(
        ExperimentModule.MODULE_1,
        Module1Condition.NO_SURT,
        reference,
        context=ExitContext.TRAINING,
    )
    subject.observe(observation(250.0))

    assert subject.presentation is not None
    assert subject.presentation.lc_event_id == "training-exit-1"
    payload = json.loads(subject.events[0].payload_json)
    assert payload["context"] == "TRAINING"
    assert payload["module"] is None
