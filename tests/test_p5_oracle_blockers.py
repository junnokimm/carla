from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import carla
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceStatus, ExitTerminationReason
from src.experiment.exit_route import ExitRouteError
from src.logging.research_event import ResearchEvent
from src.scenario.research_exit_runtime import ResearchExitObservationSource
from src.scenario.research_noa_persistence import (
    PendingResearchEvent,
    ResearchNoAPersistence,
    ResearchSnapshotSample,
)
from src.vehicle.carla_exit_route import load_exit_route_manifest
from tests.test_p5_boundary_contracts import (
    ACTIVE,
    MANUAL,
    ReferenceRecorder,
    coordinator,
    event_payloads,
    observation,
    route,
    stamp,
)


def test_v1_dogleg_manifest_is_rejected() -> None:
    with pytest.raises(ExitRouteError, match="spacing"):
        load_exit_route_manifest(Path("config/town04_exit_routes_dev_v1.json"))


def test_unmatched_sample_cannot_arm_stale_route_cursor() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    rendered = subject.prepare_presentation(ACTIVE)
    subject.mark_presented(rendered, ACTIVE, stamp(20))
    subject.observe(observation(-1.0, -3.5, 2, matched=False))

    subject.update(
        DriverInput(exit_confirm_requested=True), ACTIVE, stamp(30), stamp(31)
    )

    assert reference.armed == []
    assert event_payloads(subject)[-1]["decision"] == "UNAVAILABLE_ROUTE"


def test_confirm_rechecks_full_initiation_envelope() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(20))
    subject.observe(observation(20.0, -3.5, 2))

    subject.update(
        DriverInput(exit_confirm_requested=True), ACTIVE, stamp(30), stamp(31)
    )

    assert reference.armed == []
    assert event_payloads(subject)[-1]["decision"] == "UNAVAILABLE_ROUTE"


def test_presented_mode_change_cancels_offer_and_records_mode_event() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(20))

    subject.update(DriverInput(), MANUAL, stamp(30), stamp(31))
    subject.update(
        DriverInput(exit_confirm_requested=True), ACTIVE, stamp(40), stamp(41)
    )

    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    assert reference.armed == []
    assert (
        len([event for event in subject.events if event.event_type == "lc_mode_change"])
        == 2
    )


def test_unverified_target_boundary_never_sets_tl() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(20))

    subject.observe(observation(40.0, 0.0, 2, road=1191, section=1, lane=-2))

    assert all(
        payload["tL_host_monotonic_ns"] is None for payload in event_payloads(subject)
    )


def test_manual_missed_exit_emits_nochange_once() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(Module1Condition.NO_SURT, reference)
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(20))

    subject.observe(
        replace(
            observation(-1.0, -3.5, 2, road=1184, section=0, lane=-4, matched=False),
            route_reference_distance_m=4.0,
            route_reference_half_width_m=1.75,
        )
    )
    subject.observe(
        replace(
            observation(-1.0, -3.5, 3, road=1184, section=0, lane=-4, matched=False),
            route_reference_distance_m=4.0,
            route_reference_half_width_m=1.75,
        )
    )

    completed = [
        event for event in subject.events if event.event_type == "lc_completed"
    ]
    assert len(completed) == 1
    assert json.loads(completed[0].payload_json)["decision"] == "MANUAL_NOCHANGE"


def test_pending_events_are_stably_sorted_by_host_time() -> None:
    subject = object.__new__(ResearchNoAPersistence)
    written: list[str] = []
    subject._pending_events = []
    subject._next_enqueue_order = 0
    subject._record_event = lambda timestamp, event: written.append(event.event_type)
    subject.enqueue_event(PendingResearchEvent(stamp(30), ResearchEvent("later")))
    subject.enqueue_event(PendingResearchEvent(stamp(20), ResearchEvent("first")))
    subject.enqueue_event(PendingResearchEvent(stamp(20), ResearchEvent("second")))

    subject.flush_events()

    assert written == ["first", "second", "later"]


class SnapshotHolder:
    def __init__(self, sample: ResearchSnapshotSample) -> None:
        self.last_snapshot_sample = sample


def snapshot_sample(
    x: float,
    y: float,
    *,
    right_transform: carla.Transform | None,
) -> ResearchSnapshotSample:
    return ResearchSnapshotSample(
        stamp(10),
        carla.Transform(carla.Location(x=x, y=y)),
        39,
        0,
        -3,
        3.5,
        carla.Transform(carla.Location(x=x, y=0.0)),
        -4,
        3.5,
        right_transform,
        carla.VehicleControl(),
        "off",
    )


def test_observation_source_preserves_cursor_but_marks_projection_failure_unmatched() -> (
    None
):
    holder = SnapshotHolder(
        snapshot_sample(
            5.0,
            0.0,
            right_transform=carla.Transform(carla.Location(x=5.0, y=-3.5)),
        )
    )
    source = ResearchExitObservationSource(route(), holder)
    first = source.observe()
    cursor = source.cursor
    holder.last_snapshot_sample = snapshot_sample(
        5.0,
        100.0,
        right_transform=carla.Transform(carla.Location(x=5.0, y=-3.5)),
    )

    unmatched = source.observe()

    assert first.route_matched is True
    assert unmatched.route_matched is False
    assert unmatched.route_distance_m == -1.0
    assert source.cursor is cursor


def test_missing_paired_target_transform_is_unknown_not_zero() -> None:
    source = ResearchExitObservationSource(
        route(), SnapshotHolder(snapshot_sample(5.0, 0.0, right_transform=None))
    )

    observed = source.observe()

    assert observed.lateral_offset_m is None
    assert observed.target_boundary_valid is False


def test_indicator_onset_uses_actual_off_to_right_edge() -> None:
    subject = coordinator(Module1Condition.NO_SURT, ReferenceRecorder())
    subject.observe(replace(observation(5.0, -3.5, 1), indicator="right"))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(20))
    subject.observe(replace(observation(6.0, -3.5, 2), indicator="off"))
    subject.observe(replace(observation(7.0, -3.5, 3), indicator="right"))

    onset = [
        event for event in subject.events if event.event_type == "lc_indicator_onset"
    ]
    assert len(onset) == 1
    assert onset[0].timestamp.host.monotonic_ns == 30


def test_observation_gap_records_prior_and_current_frame() -> None:
    subject = coordinator(Module1Condition.NO_SURT, ReferenceRecorder())
    subject.observe(observation(5.0, -3.5, 10))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(20))

    subject.observe(observation(6.0, -3.5, 13))

    payload = event_payloads(subject)[-1]
    assert payload["observation_gap_previous_frame"] == 10
    assert payload["observation_gap_current_frame"] == 13


def test_stage_failure_before_timeout_does_not_fabricate_noresponse() -> None:
    subject = coordinator(Module1Condition.NO_SURT, ReferenceRecorder())
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(20))

    subject.finish(ExitTerminationReason.ERROR, stamp(30))

    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    assert all(
        payload["decision"] != "NORESPONSE" for payload in event_payloads(subject)
    )


def test_module2_manual_mode_toggle_never_arms_assistance() -> None:
    reference = ReferenceRecorder()
    subject = coordinator(
        Module2Condition.MANUAL,
        reference,
        module=ExperimentModule.MODULE_2,
    )
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(20))

    subject.update(DriverInput(activation_requested=True), ACTIVE, stamp(30), stamp(31))

    assert subject.status is ExitAssistanceStatus.INTERRUPTED
    assert reference.armed == []


def test_manual_observed_lane_change_settles_on_target_lane() -> None:
    subject = coordinator(Module1Condition.NO_SURT, ReferenceRecorder())
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(MANUAL), MANUAL, stamp(20))
    subject.observe(observation(6.0, -3.0, 2))
    assert subject.lane_change_in_progress is True

    subject.observe(observation(20.0, 0.0, 3, lane=-4))

    assert subject.lane_change_in_progress is False
