from __future__ import annotations

import carla
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.lane_change import LaneChangeDirection
from tests.test_lane_change_execution_lifecycle import make_rig, sample


def _observe_source_position(
    direction: LaneChangeDirection,
    positions: tuple[float, ...],
) -> list[str]:
    rig = make_rig(direction)
    sign = -1.0 if direction is LaneChangeDirection.LEFT else 1.0
    for frame, position in enumerate(positions, start=11):
        rig.persistence.source.last_snapshot_sample = sample(
            frame,
            y=position * sign,
            lane_id=-3,
            lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
            left_lane_id=-2,
            left_transform=carla.Transform(carla.Location(x=10.0, y=-3.5)),
            right_lane_id=-4,
            right_transform=carla.Transform(carla.Location(x=10.0, y=3.5)),
        )
        rig.observer.after_control_applied()
    return [
        payload["status"]
        for event, payload in rig.persistence.events
        if event == "lane_change_geometric_sample"
    ]


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_millimeter_jitter_within_source_lane_remains_observed(
    direction: LaneChangeDirection,
) -> None:
    statuses = _observe_source_position(direction, (0.002, -0.002, 0.001, -0.001))

    assert statuses == ["OBSERVED"] * 4


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_sustained_requested_motion_before_target_boundary_remains_observed(
    direction: LaneChangeDirection,
) -> None:
    statuses = _observe_source_position(direction, (0.5, 1.0, 1.5))

    assert statuses == ["OBSERVED"] * 3


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_target_corridor_outside_to_inside_transition_marks_progress(
    direction: LaneChangeDirection,
) -> None:
    statuses = _observe_source_position(direction, (1.5, 1.8))

    assert statuses == ["OBSERVED", "IN_PROGRESS"]


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_direct_completed_sample_does_not_invent_intermediate_progress(
    direction: LaneChangeDirection,
) -> None:
    rig = make_rig(direction)
    sign = -1.0 if direction is LaneChangeDirection.LEFT else 1.0
    target_id = -2 if direction is LaneChangeDirection.LEFT else -4
    rig.persistence.source.last_snapshot_sample = sample(
        15,
        y=3.5 * sign,
        lane_id=target_id,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=3.5 * sign)),
        left_lane_id=-3 if direction is LaneChangeDirection.RIGHT else None,
        left_transform=(
            carla.Transform(carla.Location(x=10.0, y=0.0))
            if direction is LaneChangeDirection.RIGHT
            else None
        ),
        right_lane_id=-3 if direction is LaneChangeDirection.LEFT else None,
        right_transform=(
            carla.Transform(carla.Location(x=10.0, y=0.0))
            if direction is LaneChangeDirection.LEFT
            else None
        ),
    )

    rig.observer.after_control_applied()

    lifecycle = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert lifecycle == ["REQUESTED", "SUBMITTED_TO_TM", "COMPLETED"]


@pytest.mark.parametrize(
    ("actual_road", "actual_right_lane"),
    [(1184, -4), (41, -5)],
)
def test_unverified_geometry_records_actual_raw_identity_and_pose(
    actual_road: int,
    actual_right_lane: int,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=65.75,
        y=float("nan"),
        yaw=17.0,
        road_id=actual_road,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=65.75, y=0.0)),
        right_lane_id=actual_right_lane,
        right_road_id=actual_road,
        right_transform=carla.Transform(carla.Location(x=65.75, y=3.5)),
    )

    rig.observer.after_control_applied()

    observed = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_geometric_sample"
    ][-1]
    assert observed["expected_source_identity"] == [36, 0, -3]
    assert observed["expected_target_identity"] == [36, 0, -4]
    assert observed["actual_current_identity"] == [actual_road, 0, -3]
    assert observed["actual_right_identity"] == [actual_road, 0, actual_right_lane]
    assert observed["actual_vehicle_x"] == pytest.approx(65.75)
    assert observed["actual_vehicle_y"] is None
    assert observed["actual_vehicle_heading_degrees"] == pytest.approx(17.0)
    assert observed["actual_frame"] == 11
    assert observed["actual_host_capture_started_monotonic_ns"] == 1108
    assert observed["actual_host_capture_completed_monotonic_ns"] == 1109


def test_new_request_while_unverified_reports_required_recovery() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        y=0.0,
        road_id=1184,
        lane_id=-3,
        lane_transform=carla.Transform(carla.Location(x=10.0, y=0.0)),
    )
    rig.observer.after_control_applied()

    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))

    requests = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_request"
    ]
    assert requests[-1]["disposition"] == "REJECTED_ONGOING"
    assert requests[-1]["active_request_id"] == requests[0]["request_id"]
    assert requests[-1]["latched_reason"] == "GEOMETRY_CONTINUITY_UNAVAILABLE"
    assert requests[-1]["pending_physical_outcome"] == "UNKNOWN"
    assert requests[-1]["recovery_required"] is True
    assert requests[-1]["recovery_inputs"] == "N_OR_BRAKE_OR_STEER"
    assert requests[0]["baseline_vehicle_x"] == pytest.approx(10.0)
    assert requests[0]["baseline_vehicle_y"] == pytest.approx(0.0)
    assert requests[0]["baseline_vehicle_heading_degrees"] == pytest.approx(0.0)

    rig.persistence.source.last_snapshot_sample = None
    rig.observer.after_control_applied()
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.RIGHT))
    requests = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_request"
    ]
    assert requests[-1]["latched_reason"] == "GEOMETRY_CONTINUITY_UNAVAILABLE"


def test_requested_progress_uses_current_road_heading_not_frozen_tangent() -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    for frame, x, y, lane in ((11, 10.0, -10.0, -3), (12, 8.2, -9.0, -3),
                               (13, 6.5, -8.0, -4)):
        source = carla.Transform(carla.Location(x=10.0, y=y), carla.Rotation(yaw=90))
        target = carla.Transform(carla.Location(x=6.5, y=y), carla.Rotation(yaw=90))
        rig.persistence.source.last_snapshot_sample = sample(
            frame, x=x, y=y, yaw=90.0, lane_id=lane,
            lane_transform=source if lane == -3 else target,
            right_lane_id=-4 if lane == -3 else None,
            right_transform=target if lane == -3 else None,
            left_lane_id=-3 if lane == -4 else None,
            left_transform=source if lane == -4 else None,
        )
        rig.observer.after_control_applied()
    lifecycle = [payload["lifecycle"] for kind, payload in rig.persistence.events
                 if kind == "lane_change_lifecycle"]
    assert lifecycle == ["REQUESTED", "SUBMITTED_TO_TM", "IN_PROGRESS", "COMPLETED"]
