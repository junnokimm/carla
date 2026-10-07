from __future__ import annotations

import json
from pathlib import Path

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
)
from src.experiment.exit_route import ExitRoute
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.vehicle.carla_exit_route import load_exit_route_manifest

ACTIVE = AutomationState(
    AutomationAvailability.AVAILABLE,
    DrivingControlMode.NOA_ACTIVE,
)
MANUAL = AutomationState(
    AutomationAvailability.AVAILABLE,
    DrivingControlMode.MANUAL,
)


class RouteReference:
    def __init__(self) -> None:
        self.armed: list[int] = []

    def arm(self, route: ExitRoute, point_index: int) -> None:
        self.armed.append(point_index)

    def cancel(self) -> None:
        pass


def timestamp(value: int) -> TimestampEnvelope:
    return TimestampEnvelope(HostClockTimestamp(value, value))


def actual_route() -> ExitRoute:
    return load_exit_route_manifest(
        Path("config/town04_exit_routes_dev_v3.json")
    ).route("town04-exit-39")


def coordinator(
    route: ExitRoute, reference: RouteReference
) -> ExitAssistanceCoordinator:
    return ExitAssistanceCoordinator(
        ExitAssistanceConfig(
            ExperimentModule.MODULE_1,
            Module1Condition.NO_SURT,
            ExitContext.MODULE,
            "actual-manifest-navigation",
            route,
            1000.0,
            5.0,
            0.15,
        ),
        reference,
    )


def observation(route: ExitRoute, station: float, frame: int) -> ExitObservation:
    point_index = min(
        range(len(route.points)),
        key=lambda index: abs(route.points[index].distance_m - station),
    )
    point = route.points[point_index]
    return ExitObservation(
        timestamp(frame),
        point.distance_m,
        None,
        point.road_id,
        point.section_id,
        point.lane_id,
        frame,
        frame / 10.0,
        route_matched=True,
        route_point_index=point_index,
        target_boundary_valid=False,
    )


def test_actual_manifest_confirms_at_fork_minus_one_kilometer() -> None:
    route = actual_route()
    reference = RouteReference()
    subject = coordinator(route, reference)
    subject.observe(observation(route, route.fork_distance_m - 1000.0, 1))

    assert subject.status is ExitAssistanceStatus.DRAFT
    rendered = subject.prepare_presentation(ACTIVE)
    subject.mark_presented(rendered, ACTIVE, timestamp(20))
    subject.update(
        DriverInput(exit_confirm_requested=True),
        ACTIVE,
        timestamp(30),
        timestamp(31),
    )

    assert subject.status is ExitAssistanceStatus.CONFIRMED
    assert reference.armed
    payload = json.loads(subject.events[-1].payload_json)
    assert payload["t0_host_monotonic_ns"] == 20


def test_actual_manifest_rejects_first_confirmation_after_blend_starts() -> None:
    route = actual_route()
    reference = RouteReference()
    subject = coordinator(route, reference)
    late_station = next(
        point.distance_m
        for point in route.points
        if point.distance_m > route.confirmation_end
    )
    subject.observe(observation(route, late_station, 1))

    assert subject.status is ExitAssistanceStatus.WAITING
    assert subject.presentation is None
    assert reference.armed == []


def test_manual_branch_receives_same_one_kilometer_navigation() -> None:
    route = actual_route()
    subject = coordinator(route, RouteReference())
    subject.observe(observation(route, route.fork_distance_m - 1000.0, 1))

    rendered = subject.prepare_presentation(MANUAL)
    subject.mark_presented(rendered, MANUAL, timestamp(20))

    assert rendered.recommendation is False
    assert subject.status is ExitAssistanceStatus.PRESENTED
