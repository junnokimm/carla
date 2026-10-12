from __future__ import annotations

import json

import carla

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.context import ExperimentModule, Module1Condition
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
)
from src.experiment.exit_route import ExitRoute, RoutePoint
from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitObservationSource,
    ResearchExitViewBinding,
)
from src.scenario.research_noa_persistence import ResearchSnapshotSample


def stamp(seconds: float) -> TimestampEnvelope:
    value = round(seconds * 1_000_000_000)
    return TimestampEnvelope(HostClockTimestamp(value, value))


class Reference:
    def __init__(self) -> None:
        self.cancel_count = 0

    def arm(self, route: ExitRoute, point_index: int) -> None:
        raise AssertionError("blocked execution must not arm a route")

    def cancel(self) -> None:
        self.cancel_count += 1


def route() -> ExitRoute:
    return ExitRoute(
        route_id="timed",
        route_version="v3",
        map_name="Town04",
        source_lane_id=-3,
        target_lane_id=-4,
        fork_distance_m=1200.0,
        points=(
            RoutePoint(0.0, 0.0, 0.0, 0.0, 39, 0, -3, 3.5),
            RoutePoint(1000.0, 0.0, 0.0, 1000.0, 39, 0, -4, 3.5),
            RoutePoint(1200.0, 0.0, 0.0, 1200.0, 39, 0, -4, 3.5),
        ),
        through_segments=(),
        exit_segments=((39, 0, -4),),
        initiation_end_m=1000.0,
    )


def observation(seconds: float, distance_m: float = 300.0) -> ExitObservation:
    return ExitObservation(
        timestamp=stamp(seconds),
        route_distance_m=distance_m,
        lateral_offset_m=-3.5,
        road_id=39,
        section_id=0,
        lane_id=-3,
        carla_frame=round(seconds * 1000),
        carla_simulation_seconds=seconds,
        target_boundary_valid=True,
    )


def coordinator() -> ExitAssistanceCoordinator:
    clock = SessionClock(stamp(0.0).host)
    subject = ExitAssistanceCoordinator(
        ExitAssistanceConfig(
            module=ExperimentModule.MODULE_1,
            condition=Module1Condition.NO_SURT,
            context=ExitContext.MODULE,
            lc_event_id="timed-1",
            route=route(),
            navigation_trigger_distance_m=1000.0,
            response_timeout_s=5.0,
            lateral_onset_threshold_m=0.15,
            eligibility_open_s=10.0,
            eligibility_close_s=20.0,
            assisted_execution_enabled=False,
        ),
        Reference(),
        session_clock=clock,
    )
    subject.start_active_drive(stamp(0.0))
    return subject


def test_timing_requires_route_and_inclusive_open_close_boundaries() -> None:
    subject = coordinator()

    subject.observe(observation(0.0))
    assert subject.status is ExitAssistanceStatus.WAITING
    subject.observe(observation(9.999, 350.0))
    assert subject.status is ExitAssistanceStatus.WAITING
    subject.observe(observation(10.0, 400.0))
    assert subject.status is ExitAssistanceStatus.DRAFT

    state = AutomationState(
        AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
    )
    subject.mark_presented(subject.prepare_presentation(state), state, stamp(20.0))

    assert subject.status is ExitAssistanceStatus.PRESENTED
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["eligibility_elapsed_s"] == 20.0
    assert payload["t0_host_monotonic_ns"] == 20_000_000_000


def test_closed_window_without_presentation_is_explicit_not_presented() -> None:
    subject = coordinator()

    subject.observe(observation(20.001))

    assert subject.status is ExitAssistanceStatus.NOT_PRESENTED
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["outcome"] == "NOT_PRESENTED"
    assert payload["termination_reason"] == "ELIGIBILITY_WINDOW_CLOSED"


def test_early_exit_or_miss_does_not_consume_repeated_approach() -> None:
    subject = coordinator()

    subject.observe(
        ExitObservation(
            timestamp=stamp(5.0),
            route_distance_m=1200.0,
            lateral_offset_m=-3.5,
            road_id=39,
            section_id=0,
            lane_id=-4,
            carla_frame=5,
            carla_simulation_seconds=5.0,
            route_matched=True,
            target_boundary_valid=True,
        )
    )
    assert subject.status is ExitAssistanceStatus.WAITING

    subject.observe(observation(10.0, 300.0))

    assert subject.status is ExitAssistanceStatus.DRAFT


class BindingInteraction:
    state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )

    def after_control_applied(self) -> None:
        return


class BindingSource:
    def __init__(self, timestamp: TimestampEnvelope) -> None:
        self.timestamp = timestamp

    def get_host_timestamp(self) -> TimestampEnvelope:
        return self.timestamp


class BindingPersistence:
    def __init__(self, timestamp: TimestampEnvelope) -> None:
        self.source = BindingSource(timestamp)


def test_prepare_declines_expired_draft_before_draw() -> None:
    subject = coordinator()
    subject.observe(observation(20.0))
    persistence = BindingPersistence(stamp(20.001))
    observer = ResearchExitInteractionObserver(
        BindingInteraction(), subject, None, persistence
    )
    binding = ResearchExitViewBinding(observer, persistence, None)

    rendered = binding.prepare_presentation()

    assert rendered is None
    assert subject.status is ExitAssistanceStatus.NOT_PRESENTED


def test_visible_frame_crossing_close_records_presented_deviation() -> None:
    subject = coordinator()
    subject.observe(observation(20.0))
    rendered = subject.prepare_presentation(BindingInteraction.state)

    subject.mark_presented(rendered, BindingInteraction.state, stamp(20.001))

    assert subject.status is ExitAssistanceStatus.PRESENTED
    assert [event.event_type for event in subject.events[-2:]] == [
        "lc_presented",
        "lc_presentation_timing_deviation",
    ]
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["presentation_window_status"] == (
        "OUTSIDE_WINDOW_AFTER_VISIBLE_FRAME"
    )


class SnapshotSource:
    last_snapshot_sample: ResearchSnapshotSample | None = None


def snapshot(seconds: float, x: float, lane_id: int) -> ResearchSnapshotSample:
    transform = carla.Transform(carla.Location(x=x, y=0.0))
    return ResearchSnapshotSample(
        stamp(seconds),
        transform,
        39,
        0,
        lane_id,
        3.5,
        transform,
        -4,
        3.5,
        carla.Transform(carla.Location(x=x, y=-3.5)),
        carla.VehicleControl(),
        "off",
        70.0,
    )


def test_waiting_late_pass_resets_actual_projection_for_repeated_approach() -> None:
    subject = coordinator()
    source = SnapshotSource()
    observations = ResearchExitObservationSource(route(), source)
    persistence = BindingPersistence(stamp(10.0))
    observer = ResearchExitInteractionObserver(
        BindingInteraction(), subject, observations, persistence
    )
    source.last_snapshot_sample = snapshot(10.0, 1100.0, -2)
    observer.after_control_applied()
    source.last_snapshot_sample = snapshot(11.0, 300.0, -3)

    observer.after_control_applied()

    assert subject.status is ExitAssistanceStatus.DRAFT
