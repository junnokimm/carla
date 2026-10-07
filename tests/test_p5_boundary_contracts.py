from __future__ import annotations

import json
from dataclasses import replace
from math import inf
from pathlib import Path
from typing import assert_never

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
)
from src.experiment.exit_route import (
    ExitRoute,
    ExitRouteError,
    RoutePoint,
    project_route,
)
from src.experiment.lane_geometry import PlanarPose
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.scenario.research_exit_config import (
    ResearchExitAssistanceConfig,
    ResearchExitConfigError,
)
from src.vehicle.carla_exit_route import route_data_sha256

ACTIVE = AutomationState(
    AutomationAvailability.AVAILABLE,
    DrivingControlMode.NOA_ACTIVE,
)
MANUAL = AutomationState(
    AutomationAvailability.AVAILABLE,
    DrivingControlMode.MANUAL,
)


class ReferenceRecorder:
    def __init__(self) -> None:
        self.armed: list[tuple[ExitRoute, int]] = []
        self.cancel_count = 0

    def arm(self, route: ExitRoute, point_index: int) -> None:
        self.armed.append((route, point_index))

    def cancel(self) -> None:
        self.cancel_count += 1


def stamp(value: int) -> TimestampEnvelope:
    return TimestampEnvelope(HostClockTimestamp(value, value + 1_000))


def route() -> ExitRoute:
    return ExitRoute(
        route_id="exit",
        route_version="v1",
        map_name="Town04",
        source_lane_id=-3,
        target_lane_id=-4,
        fork_distance_m=30.0,
        points=(
            RoutePoint(0.0, 0.0, 0.0, 0.0, 39, 0, -3, 3.5),
            RoutePoint(10.0, 0.0, 0.0, 10.0, 39, 0, -3, 3.5),
            RoutePoint(20.0, -1.75, 0.0, 20.0, 39, 0, -4, 3.5),
            RoutePoint(30.0, -3.5, 0.0, 30.0, 39, 0, -4, 3.5),
            RoutePoint(40.0, -3.5, 0.0, 40.0, 1191, 1, -2, 3.5),
            RoutePoint(60.0, -3.5, 0.0, 60.0, 33, 0, 2, 3.5),
        ),
        through_segments=((1184, 0, -3), (1184, 0, -4)),
        exit_segments=((1191, 1, -2), (33, 0, 2)),
        initiation_start_m=0.0,
        initiation_end_m=15.0,
        completion_distance_m=55.0,
    )


def observation(
    distance: float,
    lateral: float,
    frame: int,
    *,
    road: int = 39,
    section: int = 0,
    lane: int | None = -3,
    matched: bool = True,
    target_boundary_valid: bool | None = None,
) -> ExitObservation:
    return ExitObservation(
        timestamp=stamp(frame * 10),
        route_distance_m=distance,
        lateral_offset_m=lateral,
        road_id=road,
        section_id=section,
        lane_id=lane,
        carla_frame=frame,
        carla_simulation_seconds=frame / 10,
        route_matched=matched,
        route_point_index=1,
        target_boundary_valid=(
            road == 39 and section == 0 and lane in (-3, -4)
            if target_boundary_valid is None
            else target_boundary_valid
        ),
    )


def coordinator(
    condition: Module1Condition | Module2Condition,
    reference: ReferenceRecorder,
    *,
    module: ExperimentModule = ExperimentModule.MODULE_1,
) -> ExitAssistanceCoordinator:
    return ExitAssistanceCoordinator(
        ExitAssistanceConfig(
            module=module,
            condition=condition,
            context=ExitContext.MODULE,
            lc_event_id="exit-1",
            route=route(),
            navigation_trigger_distance_m=100.0,
            response_timeout_s=5.0,
            lateral_onset_threshold_m=0.15,
        ),
        reference,
    )


def event_payloads(subject: ExitAssistanceCoordinator) -> list[dict[str, object]]:
    return [json.loads(event.payload_json) for event in subject.events]


def test_projection_rejects_outside_corridor_and_preserves_cursor_progress() -> None:
    selected = route()
    with pytest.raises(ExitRouteError):
        project_route(selected, PlanarPose(10.0, 100.0, 0.0), corridor_m=8.0)

    first = project_route(selected, PlanarPose(29.0, -3.5, 0.0), corridor_m=8.0)
    with pytest.raises(ExitRouteError):
        project_route(
            selected,
            PlanarPose(9.0, 0.0, 0.0),
            cursor=first,
            corridor_m=8.0,
        )


def test_semantic_hash_changes_for_identity_topology_and_envelope() -> None:
    selected = route()
    baseline = route_data_sha256((selected,))

    assert route_data_sha256((replace(selected, route_id="other"),)) != baseline
    assert (
        route_data_sha256((replace(selected, through_segments=((999, 0, -3),)),))
        != baseline
    )
    assert route_data_sha256((replace(selected, initiation_end_m=14.0),)) != baseline


def test_prepare_and_mark_freeze_the_exact_rendered_assisted_snapshot() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))

    rendered = subject.prepare_presentation(ACTIVE)

    assert rendered.recommendation is True
    subject.observe(observation(6.0, -3.5, 2))
    with pytest.raises(ValueError):
        subject.mark_presented(rendered, ACTIVE, stamp(30))
    current = subject.prepare_presentation(ACTIVE)
    subject.mark_presented(current, ACTIVE, stamp(40))
    assert subject.status is ExitAssistanceStatus.PRESENTED


def test_stationary_source_lane_is_baseline_not_movement() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(
        Module2Condition.MANUAL, reference, module=ExperimentModule.MODULE_2
    )
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(MANUAL)
    subject.mark_presented(rendered, MANUAL, stamp(20))

    subject.observe(observation(6.0, -3.5, 2))

    assert not any(event.event_type == "lc_maneuver_start" for event in subject.events)
    assert subject.lane_change_in_progress is False


def test_manual_branch_does_not_timeout_without_assisted_recommendation() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(MANUAL)
    subject.mark_presented(rendered, MANUAL, stamp(20))

    subject.update(DriverInput(), MANUAL, stamp(6_000_000_021), stamp(6_000_000_022))

    assert subject.status is ExitAssistanceStatus.PRESENTED


def test_cancelled_automatic_assistance_keeps_observing_physical_exit_outcome() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(ACTIVE)
    subject.mark_presented(rendered, ACTIVE, stamp(20))
    subject.update(
        DriverInput(exit_confirm_requested=True), ACTIVE, stamp(30), stamp(31)
    )
    subject.update(DriverInput(), MANUAL, stamp(40), stamp(41))

    subject.observe(observation(20.0, -1.5, 2))
    subject.observe(observation(40.0, 0.0, 3, road=1191, section=1, lane=-2))
    subject.observe(observation(56.0, 0.0, 4, road=33, section=0, lane=2))

    assert subject.status is ExitAssistanceStatus.COMPLETED
    assert event_payloads(subject)[-1]["outcome"] == "EXIT"
    assert reference.cancel_count == 1


def test_confirm_records_independent_input_receipt_and_commit_timestamps() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(ACTIVE)
    subject.mark_presented(rendered, ACTIVE, stamp(20))

    subject.update(
        DriverInput(exit_confirm_requested=True), ACTIVE, stamp(30), stamp(31)
    )

    payload = event_payloads(subject)[-1]
    assert payload["input_receipt_host_monotonic_ns"] == 30
    assert payload["decision_commit_host_monotonic_ns"] == 31


def test_late_confirm_after_observed_movement_is_unavailable_and_never_arms() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(ACTIVE)
    subject.mark_presented(rendered, ACTIVE, stamp(20))
    subject.observe(observation(6.0, -3.0, 2))

    subject.update(
        DriverInput(exit_confirm_requested=True), ACTIVE, stamp(30), stamp(31)
    )

    assert reference.armed == []
    assert event_payloads(subject)[-1]["decision"] == "UNAVAILABLE_MOVEMENT_STARTED"


def test_frame_regression_is_recorded_as_gap_without_fabricated_progress() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.SURT, reference)
    subject.observe(observation(5.0, -3.5, 10))
    rendered = subject.prepare_presentation(MANUAL)
    subject.mark_presented(rendered, MANUAL, stamp(20))

    subject.observe(observation(6.0, -3.0, 9))

    assert subject.status is ExitAssistanceStatus.PRESENTED
    assert subject.events[-1].event_type == "lc_observation_gap"


@pytest.mark.parametrize("value", [0.0, -1.0, inf])
def test_config_requires_finite_positive_values(value: float) -> None:
    with pytest.raises(ResearchExitConfigError):
        ResearchExitAssistanceConfig(
            Path("manifest.json"),
            "route",
            "event",
            navigation_trigger_distance_m=value,
        )


def test_config_normalizes_ascii_keys_to_lowercase() -> None:
    config = ResearchExitAssistanceConfig(
        Path("manifest.json"),
        "route",
        "event",
        confirm_key="C",
        reject_key="R",
    )

    assert config.confirm_key == "c"
    assert config.reject_key == "r"


@pytest.mark.parametrize(
    ("module", "condition", "mode", "action", "expected", "armed"),
    [
        *[
            (ExperimentModule.MODULE_1, condition, ACTIVE, action, expected, armed)
            for condition in Module1Condition
            for action, expected, armed in (
                ("confirm", ExitAssistanceStatus.CONFIRMED, True),
                ("reject", ExitAssistanceStatus.REJECTED, False),
                ("noresponse", ExitAssistanceStatus.NORESPONSE, False),
            )
        ],
        (
            ExperimentModule.MODULE_1,
            Module1Condition.NO_SURT,
            MANUAL,
            "nochange",
            ExitAssistanceStatus.PRESENTED,
            False,
        ),
        (
            ExperimentModule.MODULE_1,
            Module1Condition.SURT,
            MANUAL,
            "manualchange",
            ExitAssistanceStatus.PRESENTED,
            False,
        ),
        (
            ExperimentModule.MODULE_2,
            Module2Condition.NOA_L2,
            ACTIVE,
            "confirm",
            ExitAssistanceStatus.CONFIRMED,
            True,
        ),
        (
            ExperimentModule.MODULE_2,
            Module2Condition.NOA_L2,
            ACTIVE,
            "reject",
            ExitAssistanceStatus.REJECTED,
            False,
        ),
        (
            ExperimentModule.MODULE_2,
            Module2Condition.MANUAL,
            MANUAL,
            "manualchange",
            ExitAssistanceStatus.PRESENTED,
            False,
        ),
    ],
)
def test_module_mode_decision_matrix(
    module: ExperimentModule,
    condition: Module1Condition | Module2Condition,
    mode: AutomationState,
    action: str,
    expected: ExitAssistanceStatus,
    armed: bool,
) -> None:
    reference = ReferenceRecorder()
    subject = coordinator(condition, reference, module=module)
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(mode)
    subject.mark_presented(rendered, mode, stamp(20))

    match action:
        case "confirm":
            subject.update(
                DriverInput(exit_confirm_requested=True), mode, stamp(30), stamp(31)
            )
        case "reject":
            subject.update(
                DriverInput(exit_reject_requested=True), mode, stamp(30), stamp(31)
            )
        case "noresponse":
            subject.update(
                DriverInput(), mode, stamp(6_000_000_021), stamp(6_000_000_022)
            )
        case "manualchange":
            subject.observe(observation(6.0, -3.0, 2))
        case "nochange":
            subject.observe(observation(6.0, -3.5, 2))
        case unreachable:
            assert_never(unreachable)

    assert subject.status is expected
    assert bool(reference.armed) is armed
