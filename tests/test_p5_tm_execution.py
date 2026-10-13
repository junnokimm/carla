from __future__ import annotations

import json
from dataclasses import dataclass, replace

import carla
import pytest

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
)
from src.experiment.exit_route import ExitRoute
from src.experiment.lane_change import LaneChangeDirection
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitObservationSource,
)
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_lane_change_execution_lifecycle import (
    Interaction,
    Rig,
    drive_to_completion,
    make_rig,
    sample,
)
from tests.test_p5_boundary_contracts import observation, route, stamp

ACTIVE = AutomationState(
    AutomationAvailability.AVAILABLE,
    DrivingControlMode.NOA_ACTIVE,
)


class ReferenceRecorder:
    def __init__(self) -> None:
        self.armed: list[tuple[ExitRoute, int]] = []
        self.cancel_count = 0

    def arm(self, selected_route: ExitRoute, point_index: int) -> None:
        self.armed.append((selected_route, point_index))

    def cancel(self) -> None:
        self.cancel_count += 1


class DeliveryAcknowledgementLost(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IntegratedRig:
    lane: Rig
    reference: ReferenceRecorder
    coordinator: ExitAssistanceCoordinator
    observer: ResearchExitInteractionObserver


def integrated_rig() -> IntegratedRig:
    lane = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    selected_route = replace(
        route(),
        points=tuple(replace(point, road_id=36) for point in route().points),
    )
    reference = ReferenceRecorder()
    coordinator = ExitAssistanceCoordinator(
        ExitAssistanceConfig(
            module=ExperimentModule.MODULE_1,
            condition=Module1Condition.NO_SURT,
            context=ExitContext.MODULE,
            lc_event_id="route-event",
            route=selected_route,
            navigation_trigger_distance_m=100.0,
            response_timeout_s=5.0,
            lateral_onset_threshold_m=0.15,
        ),
        reference,
        assisted_submission=lane.observer.submit_confirmed_p5,
        submission_clock=lane.persistence.source.get_host_timestamp,
    )
    observer = ResearchExitInteractionObserver(
        lane.observer,
        coordinator,
        ResearchExitObservationSource(selected_route, lane.persistence.source),
        lane.persistence,
    )
    return IntegratedRig(lane, reference, coordinator, observer)


def present(subject: IntegratedRig) -> None:
    subject.coordinator.observe(
        replace(observation(5.0, 0.0, 1), road_id=36)
    )
    rendered = subject.coordinator.prepare_presentation(ACTIVE)
    subject.coordinator.mark_presented(rendered, ACTIVE, stamp(20))


def force_calls(subject: IntegratedRig) -> list[tuple[str, int | bool | float]]:
    return [
        call
        for call in subject.lane.traffic_manager.calls
        if call[0] == "force_lane_change"
    ]


def test_accepted_p5_submits_one_right_tm_request_after_coordinator_guards() -> None:
    subject = integrated_rig()
    present(subject)

    subject.observer.update(
        DriverInput(
            exit_confirm_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )
    subject.observer.update(
        DriverInput(
            exit_confirm_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )

    requests = [
        payload
        for event_type, payload in subject.lane.persistence.events
        if event_type == "lane_change_request"
    ]
    decisions = [
        event
        for event in subject.coordinator.events
        if event.event_type == "lc_decision"
    ]
    assert force_calls(subject) == [("force_lane_change", True)]
    assert subject.reference.armed == []
    assert len(decisions) == 1
    assert len(requests) == 1
    assert requests[0]["context"] == "P5"
    assert requests[0]["lc_event_id"] == "route-event"


def test_failed_p5_submission_finishes_after_attempt_not_at_decision() -> None:
    subject = integrated_rig()
    present(subject)
    subject.lane.vehicle.current_light_state = carla.VehicleLightState.NONE
    subject.observer.update(DriverInput(
        exit_confirm_requested=True, lane_change_direction=LaneChangeDirection.RIGHT,
    ))
    finished = next(event for event in subject.coordinator.events if event.event_type == "lc_finished")
    attempts = [payload for kind, payload in subject.lane.persistence.events if kind == "lane_change_lifecycle"]
    assert finished.timestamp.host.monotonic_ns > max(payload["host_monotonic_ns"] for payload in attempts)
    payload = json.loads(finished.payload_json)
    assert payload["decision_commit_host_monotonic_ns"] < finished.timestamp.host.monotonic_ns


@pytest.mark.parametrize(
    "case",
    ["preheld", "reject", "brake", "mode", "timeout", "stale"],
)
def test_blocked_p5_input_never_reaches_tm(case: str) -> None:
    subject = integrated_rig()
    if case == "preheld":
        subject.observer.update(
            DriverInput(
                exit_confirm_requested=True,
                lane_change_direction=LaneChangeDirection.RIGHT,
            )
        )
    present(subject)
    driver_input = DriverInput(
        exit_confirm_requested=True,
        lane_change_direction=LaneChangeDirection.RIGHT,
    )
    if case == "reject":
        driver_input = replace(driver_input, exit_reject_requested=True)
    elif case == "brake":
        driver_input = replace(driver_input, brake=0.1)
    elif case == "mode":
        interaction = subject.lane.observer._interaction
        assert isinstance(interaction, Interaction)
        interaction._state = AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.MANUAL,
        )
    elif case == "timeout":
        subject.lane.persistence.source.host_ns = 6_000_000_100
    elif case == "stale":
        subject.coordinator.observe(
            replace(
                observation(5.0, 0.0, 2, matched=False),
                road_id=37,
                target_boundary_valid=False,
            )
        )

    subject.observer.update(driver_input)

    assert force_calls(subject) == []
    assert subject.reference.armed == []
    assert all(
        event_type != "lane_change_request"
        for event_type, _payload in subject.lane.persistence.events
    )


def test_p5_during_ongoing_general_request_is_rejected_without_resubmission() -> None:
    subject = integrated_rig()
    subject.lane.vehicle.current_light_state = carla.VehicleLightState.LeftBlinker
    subject.lane.observer.update(
        DriverInput(lane_change_direction=LaneChangeDirection.LEFT)
    )
    present(subject)

    subject.observer.update(
        DriverInput(
            exit_confirm_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )

    requests = [
        payload
        for event_type, payload in subject.lane.persistence.events
        if event_type == "lane_change_request"
    ]
    assert len(force_calls(subject)) == 1
    assert requests[-1]["context"] == "P5"
    assert requests[-1]["disposition"] == "REJECTED_ONGOING"
    assert subject.reference.armed == []
    assert subject.coordinator.status is ExitAssistanceStatus.INTERRUPTED
    finished = json.loads(subject.coordinator.events[-1].payload_json)
    assert finished["outcome"] == "UNVERIFIED"


def test_unverified_tm_delivery_keeps_lane_observation_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = integrated_rig()
    present(subject)

    def lose_acknowledgement(
        actor: FakeResearchVehicle,
        direction: bool,
    ) -> None:
        subject.lane.traffic_manager.calls.append(("force_lane_change", direction))
        raise DeliveryAcknowledgementLost

    monkeypatch.setattr(
        subject.lane.traffic_manager,
        "force_lane_change",
        lose_acknowledgement,
    )

    subject.observer.update(
        DriverInput(
            exit_confirm_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )

    assert force_calls(subject) == [("force_lane_change", True)]
    assert subject.lane.observer.lane_change_in_progress is True
    assert subject.coordinator.status is ExitAssistanceStatus.INTERRUPTED
    assert subject.reference.armed == []


def test_general_request_remains_available_after_completed_p5_maneuver() -> None:
    subject = integrated_rig()
    present(subject)
    subject.observer.update(
        DriverInput(
            exit_confirm_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )
    drive_to_completion(subject.lane, LaneChangeDirection.RIGHT)
    subject.lane.persistence.source.last_snapshot_sample = sample(
        20,
        y=0.0,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )

    subject.lane.vehicle.current_light_state |= carla.VehicleLightState.RightBlinker
    subject.observer.update(
        DriverInput(lane_change_direction=LaneChangeDirection.RIGHT)
    )

    requests = [
        payload
        for event_type, payload in subject.lane.persistence.events
        if event_type == "lane_change_request"
    ]
    assert len(force_calls(subject)) == 2
    assert requests[-1]["context"] == "GENERAL"
    assert requests[-1]["disposition"] == "REQUESTED"
