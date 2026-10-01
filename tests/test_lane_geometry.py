from __future__ import annotations

from math import inf, nan, pi, radians

import pytest

from src.experiment.lane_geometry import (
    LaneGeometryObservation,
    LaneGeometryValidationError,
    PlanarPose,
    compute_lane_relative_geometry,
)


def test_centered_aligned_vehicle_has_zero_errors() -> None:
    pose = PlanarPose(x=4.0, y=-2.0, yaw_rad=0.25)

    observation = compute_lane_relative_geometry(pose, pose)

    assert observation == LaneGeometryObservation(
        lateral_error_m=0.0,
        heading_error_rad=0.0,
    )


@pytest.mark.parametrize(
    ("vehicle_y", "expected_lateral_error_m"),
    [(2.5, 2.5), (-2.5, -2.5)],
)
def test_eastbound_lane_uses_positive_error_on_right_side(
    vehicle_y: float,
    expected_lateral_error_m: float,
) -> None:
    observation = compute_lane_relative_geometry(
        PlanarPose(x=10.0, y=vehicle_y, yaw_rad=0.0),
        PlanarPose(x=8.0, y=0.0, yaw_rad=0.0),
    )

    assert observation.lateral_error_m == pytest.approx(expected_lateral_error_m)


@pytest.mark.parametrize(
    ("vehicle_x", "expected_lateral_error_m"),
    [(-3.0, 3.0), (3.0, -3.0)],
)
def test_northbound_lane_preserves_right_left_semantics(
    vehicle_x: float,
    expected_lateral_error_m: float,
) -> None:
    observation = compute_lane_relative_geometry(
        PlanarPose(x=vehicle_x, y=4.0, yaw_rad=pi / 2.0),
        PlanarPose(x=0.0, y=1.0, yaw_rad=pi / 2.0),
    )

    assert observation.lateral_error_m == pytest.approx(expected_lateral_error_m)


@pytest.mark.parametrize(
    ("vehicle_yaw_rad", "expected_heading_error_rad"),
    [(radians(12.0), radians(12.0)), (radians(-12.0), radians(-12.0))],
)
def test_heading_error_uses_positive_rightward_deviation(
    vehicle_yaw_rad: float,
    expected_heading_error_rad: float,
) -> None:
    observation = compute_lane_relative_geometry(
        PlanarPose(x=0.0, y=0.0, yaw_rad=vehicle_yaw_rad),
        PlanarPose(x=0.0, y=0.0, yaw_rad=0.0),
    )

    assert observation.heading_error_rad == pytest.approx(expected_heading_error_rad)


@pytest.mark.parametrize(
    ("vehicle_yaw_deg", "lane_yaw_deg", "expected_heading_error_deg"),
    [(-179.0, 179.0, 2.0), (179.0, -179.0, -2.0)],
)
def test_heading_wraparound_returns_minimal_signed_angle(
    vehicle_yaw_deg: float,
    lane_yaw_deg: float,
    expected_heading_error_deg: float,
) -> None:
    observation = compute_lane_relative_geometry(
        PlanarPose(x=0.0, y=0.0, yaw_rad=radians(vehicle_yaw_deg)),
        PlanarPose(x=0.0, y=0.0, yaw_rad=radians(lane_yaw_deg)),
    )

    assert observation.heading_error_rad == pytest.approx(
        radians(expected_heading_error_deg)
    )


@pytest.mark.parametrize(
    ("vehicle_yaw_rad", "lane_yaw_rad"),
    [(-100.0 * pi, 0.25), (-pi, pi), (0.0, 0.0), (pi, -pi), (100.0 * pi, -0.25)],
)
def test_heading_error_is_always_in_signed_minimal_range(
    vehicle_yaw_rad: float,
    lane_yaw_rad: float,
) -> None:
    observation = compute_lane_relative_geometry(
        PlanarPose(x=0.0, y=0.0, yaw_rad=vehicle_yaw_rad),
        PlanarPose(x=0.0, y=0.0, yaw_rad=lane_yaw_rad),
    )

    assert -pi <= observation.heading_error_rad <= pi


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("x", (nan, 0.0, 0.0)),
        ("x", (inf, 0.0, 0.0)),
        ("x", (-inf, 0.0, 0.0)),
        ("y", (0.0, nan, 0.0)),
        ("y", (0.0, inf, 0.0)),
        ("y", (0.0, -inf, 0.0)),
    ],
)
def test_nonfinite_planar_coordinate_is_rejected(
    field: str,
    values: tuple[float, float, float],
) -> None:
    with pytest.raises(LaneGeometryValidationError) as caught:
        PlanarPose(*values)

    assert caught.value.field == field


@pytest.mark.parametrize("yaw_rad", [nan, inf, -inf])
def test_nonfinite_yaw_is_rejected(yaw_rad: float) -> None:
    with pytest.raises(LaneGeometryValidationError) as caught:
        PlanarPose(x=0.0, y=0.0, yaw_rad=yaw_rad)

    assert caught.value.field == "yaw_rad"


def test_repeated_geometry_calculation_is_deterministic() -> None:
    vehicle_pose = PlanarPose(x=12.0, y=-7.0, yaw_rad=1.2)
    lane_pose = PlanarPose(x=11.5, y=-6.5, yaw_rad=1.0)

    first = compute_lane_relative_geometry(vehicle_pose, lane_pose)
    second = compute_lane_relative_geometry(vehicle_pose, lane_pose)

    assert first == second
