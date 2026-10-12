import pytest

from src.experiment.lane_geometry import PlanarPose
from src.p7.geometry import ProjectionBox, smooth_lane_change, station


def test_station_reuses_right_positive_lane_frame():
    assert station(PlanarPose(10, 3, 0), PlanarPose(0, 0, 0)) == 10


def test_lead_profile_is_deterministic_and_has_zero_end_lateral_rate():
    assert smooth_lane_change(-1, 3) == (0, 0)
    assert smooth_lane_change(0, 3) == (0, 0)
    fraction, rate = smooth_lane_change(1.5, 3)
    assert fraction == pytest.approx(0.5)
    assert rate > 0
    assert smooth_lane_change(3, 3) == (1, 0)
    assert smooth_lane_change(4, 3) == (1, 0)


def test_occlusion_proxy_and_reveal_are_not_same_condition():
    lead = ProjectionBox(-0.2, -0.2, 0.2, 0.2)
    cargo = ProjectionBox(-0.05, -0.05, 0.05, 0.05)
    moved = ProjectionBox(0.3, -0.2, 0.5, 0.2)

    assert lead.contains(cargo)
    assert not lead.disjoint(cargo)
    assert moved.disjoint(cargo)
