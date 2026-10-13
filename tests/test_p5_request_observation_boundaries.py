from __future__ import annotations

import json

import carla

from src.experiment.context import Module1Condition
from src.experiment.exit_assistance import ExitAssistanceStatus
from src.experiment.lane_change import LaneChangeDirection
from tests.test_lane_change_execution_lifecycle import make_rig, sample
from tests.test_p5_boundary_contracts import (
    ACTIVE,
    ReferenceRecorder,
    coordinator,
    observation,
    stamp,
)


def test_stale_generation_at_observed_completion_is_unverified() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    assert rig.observer.submit_confirmed_p5() is True
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=1.8,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
        right_lane_id=-4,
        right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
    )
    rig.observer.after_control_applied()
    rig.backend.enter_manual_control()
    rig.persistence.source.last_snapshot_sample = sample(
        12,
        y=3.5,
        lane_id=-4,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
        left_lane_id=-3,
        left_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
    )

    rig.observer.after_control_applied()

    lifecycle = [
        payload
        for event_type, payload in rig.persistence.events
        if event_type == "lane_change_lifecycle"
    ]
    assert lifecycle[-1]["lifecycle"] == "UNVERIFIED"
    assert lifecycle[-1]["reason"] == "STALE_ACTIVATION_GENERATION_AT_COMPLETION"
    assert lifecycle[-1]["observed_lane_id"] == -4
    assert lifecycle[-1]["target_aligned"] is True
    assert rig.observer.lane_change_in_progress is False
    assert rig.vehicle.get_light_state() & carla.VehicleLightState.RightBlinker
    assert not any(
        event_type == "lane_change_signal_autooff"
        for event_type, _payload in rig.persistence.events
    )


def test_geometric_sample_records_target_reference_and_alignment_failure() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT, submit_general=False)
    assert rig.observer.submit_confirmed_p5() is True
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=12.0,
        y=3.5,
        yaw=30.0,
        lane_id=-4,
        lane_transform=carla.Transform(
            carla.Location(x=12.0, y=3.5),
            carla.Rotation(yaw=7.0),
        ),
        left_lane_id=-3,
        left_transform=carla.Transform(carla.Location(x=12.0, y=0.0)),
    )

    rig.observer.after_control_applied()

    geometric = next(
        payload
        for event_type, payload in reversed(rig.persistence.events)
        if event_type == "lane_change_geometric_sample"
    )
    assert geometric["target_alignment_failure"] in {
        "HEADING_MISALIGNED",
        "VEHICLE_STRADDLING",
    }
    assert geometric["target_lane_center_x"] == 12.0
    assert geometric["target_lane_center_y"] == 3.5
    assert geometric["target_lane_heading_degrees"] == 7.0
    assert geometric["target_lane_width_m"] == 3.5
    assert geometric["center_tolerance_m"] == 0.2
    assert geometric["heading_tolerance_rad"] == 0.1
    assert geometric["physical_completion_claimed"] is False


def test_exit_entry_is_observed_before_exit_route_completion_without_rejoin_claim() -> (
    None
):
    subject = coordinator(Module1Condition.NO_SURT, ReferenceRecorder())
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(20))

    subject.observe(observation(40.0, 0.0, 2, road=1191, section=1, lane=-2))

    entry = subject.events[-1]
    entry_payload = json.loads(entry.payload_json)
    assert entry.event_type == "lc_exit_entry_observed"
    assert entry_payload["outcome"] == "EXIT_ENTRY_OBSERVED"
    assert entry_payload["highway_rejoin_status"] == "NOT_CONFIGURED"
    assert subject.status is ExitAssistanceStatus.PRESENTED

    subject.observe(observation(56.0, 0.0, 3, road=33, section=0, lane=2))

    completed_payload = json.loads(subject.events[-1].payload_json)
    assert subject.events[-1].event_type == "lc_completed"
    assert completed_payload["outcome"] == "EXIT"
    assert completed_payload["highway_rejoin_status"] == "NOT_CONFIGURED"
    assert subject.status is ExitAssistanceStatus.COMPLETED
