"""Synthetic connected lane geometry; no live Town04 topology is implied."""

from math import atan2, cos, degrees, hypot, pi, sin
from types import SimpleNamespace

import carla
import pytest

from src.p7.moving_route import MovingRoute, angle_delta
from src.p7.types import P7Error
from tests.m2_hazard_fakes import Map, Waypoint


class CircularWaypoint(Waypoint):
    """Concentric lanes with real lane-specific forward arc-length steps."""

    def __init__(self, radius=120.0, theta=0.0, lane=-3, grade=0.0, bank=0.0):
        self.radius, self.theta = radius, theta
        self.lane_id, self.grade, self.bank = lane, grade, bank
        local_radius = radius - (lane + 3) * self.lane_width
        self.transform = carla.Transform(
            carla.Location(
                x=local_radius * sin(theta),
                y=radius - local_radius * cos(theta),
                z=grade * radius * theta,
            ),
            carla.Rotation(
                yaw=degrees(theta),
                pitch=degrees(atan2(grade * radius, local_radius)),
                roll=bank,
            ),
        )
        self.is_junction = True

    def next(self, distance):
        local_radius = self.radius - (self.lane_id + 3) * self.lane_width
        theta = self.theta + distance / hypot(local_radius, self.grade * self.radius)
        return [
            CircularWaypoint(self.radius, theta, self.lane_id, self.grade, self.bank)
        ]

    def get_left_lane(self):
        return CircularWaypoint(
            self.radius, self.theta, self.lane_id + 1, self.grade, self.bank
        )

    def get_right_lane(self):
        return None


def waypoint_map(start):
    return SimpleNamespace(get_waypoint=lambda *args, **kwargs: start)


def test_gradual_connected_curve_and_junctions_accept_large_total_heading_change():
    start = CircularWaypoint()
    trace = {}
    route = MovingRoute.select(
        waypoint_map(start), start.transform, 240, 4, diagnostics=trace
    )
    assert degrees(angle_delta(route.source[-1].transform.rotation.yaw, 0)) > 110
    assert route.side == "left"
    assert all(wp.is_junction for wp in route.source + route.target)
    assert trace["route_start"]["is_junction"] is True
    assert trace["route_selected_side"] == "left"
    assert "route_selection_failure" not in trace


def test_curve_projection_preserves_polyline_station_and_signed_lateral():
    start = CircularWaypoint()
    route = MovingRoute.select(waypoint_map(start), start.transform, 240, 4)
    for station in (0, 1.3, 48.7, 125.9, 235.2, route.stations[-1]):
        pose = route.pose(station)
        projected, lateral = route.project(pose.location)
        assert projected == pytest.approx(station, abs=2e-5)
        assert lateral == pytest.approx(0, abs=2e-5)
    station = (route.stations[20] + route.stations[21]) / 2
    pose = route.pose(station)
    right = pose.get_right_vector()
    location = pose.location + 1.2 * right
    projected, lateral = route.project(location)
    assert projected == pytest.approx(station, abs=2e-5)
    assert lateral == pytest.approx(1.2, abs=2e-5)


def test_curved_lane_change_pose_follows_target_tangent_and_surface_grade():
    start = CircularWaypoint(grade=0.06, bank=4.5)
    route = MovingRoute.select(waypoint_map(start), start.transform, 220, 4)
    station = (route.stations[25] + route.stations[26]) / 2
    for fraction in (0, 0.5, 1):
        pose = route.pose(station, fraction)
        before = route.pose(station - 0.05, fraction).location
        after = route.pose(station + 0.05, fraction).location
        displacement = after - before
        assert pose.rotation.pitch == pytest.approx(
            degrees(atan2(displacement.z, hypot(displacement.x, displacement.y))),
            abs=0.005,
        )
        assert pose.rotation.yaw == pytest.approx(
            degrees(atan2(displacement.y, displacement.x)), abs=0.005
        )
        assert pose.rotation.roll == pytest.approx(4.5)
        assert pose.location.z > 0
    projected, lateral = route.project(route.pose(station).location)
    assert projected == pytest.approx(station, abs=2e-5)
    assert lateral == pytest.approx(0, abs=2e-5)


def test_three_dimensional_projection_does_not_confuse_grade_with_horizontal_station():
    start = CircularWaypoint(grade=0.08)
    route = MovingRoute.select(waypoint_map(start), start.transform, 200, 4)
    station = (route.stations[15] + route.stations[16]) / 2
    pose = route.pose(station)
    # Displacement normal to the graded segment should not change station.
    projected, _ = route.project(pose.location + 0.5 * pose.get_up_vector())
    assert projected == pytest.approx(station, abs=2e-5)


@pytest.mark.parametrize("non_driving", [False, True])
def test_only_one_distinct_driving_successor_is_selected(non_driving):
    start = Waypoint(0)
    following = Waypoint(4)
    duplicate = Waypoint(4)
    extra = Waypoint(4, -2)
    extra.lane_type = carla.LaneType.Sidewalk
    start.next = lambda distance: (
        [following, duplicate] + ([extra] if non_driving else [])
    )
    route = MovingRoute.select(waypoint_map(start), start.transform, 12, 4)
    assert route.stations == pytest.approx([0, 4, 8, 12])


def test_distinct_driving_fork_is_deferred_with_candidate_diagnostics():
    start = Waypoint(0)
    first, second = Waypoint(4), Waypoint(4, -2)
    start.next = lambda distance: [first, first, second]
    trace = {}
    with pytest.raises(P7Error, match="LANE_UNSUITABLE:AMBIGUOUS_FORK"):
        MovingRoute.select(
            waypoint_map(start), start.transform, 12, 4, diagnostics=trace
        )
    failure = trace["route_selection_failure"]
    assert failure["path"] == "source"
    assert failure["station_m"] == 0
    assert failure["raw_successor_count"] == 3
    assert failure["unique_driving_successor_count"] == 2
    assert [item["lane_id"] for item in failure["candidates"]] == [-3, -3, -2]
    assert failure["candidates"][-1]["pose"]["y"] == 3.5
    assert failure["candidates"][-1]["local_heading_delta_deg"] == 0


def test_overlapping_positions_with_different_road_identity_are_not_deduplicated():
    start = Waypoint(0)
    first, second = Waypoint(4), Waypoint(4)
    second.road_id = 48
    start.next = lambda distance: [first, second]
    with pytest.raises(P7Error, match="AMBIGUOUS_FORK"):
        MovingRoute.select(waypoint_map(start), start.transform, 12, 4)


@pytest.mark.parametrize("no_driving", [False, True])
def test_disconnection_has_a_separate_reason_and_candidate_counts(no_driving):
    start = Waypoint(0)
    sidewalk = Waypoint(4)
    sidewalk.lane_type = carla.LaneType.Sidewalk
    start.next = lambda distance: [sidewalk] if no_driving else []
    trace = {}
    with pytest.raises(P7Error, match="LANE_UNSUITABLE:DISCONNECTED"):
        MovingRoute.select(
            waypoint_map(start), start.transform, 12, 4, diagnostics=trace
        )
    failure = trace["route_selection_failure"]
    assert failure["raw_successor_count"] == int(no_driving)
    assert failure["unique_driving_successor_count"] == 0


@pytest.mark.parametrize(
    "x,y,yaw,reason",
    [
        (0, 0, 0, "NONADVANCING"),
        (-4, 0, 0, "NONADVANCING"),
        (0, 4, 0, "NONADVANCING"),
        (4, 0, 180, "CURVATURE"),
        (4, 0, 30, "CURVATURE"),
        (4, 2, 0, "CURVATURE"),
    ],
)
def test_invalid_local_forward_geometry_is_rejected(x, y, yaw, reason):
    start, following = Waypoint(0), Waypoint(x)
    following.transform = carla.Transform(
        carla.Location(x=x, y=y), carla.Rotation(yaw=yaw)
    )
    start.next = lambda distance: [following]
    with pytest.raises(P7Error, match=f"LANE_UNSUITABLE:{reason}"):
        MovingRoute.select(waypoint_map(start), start.transform, 12, 4)


def test_consecutive_segment_turn_is_checked_even_when_waypoint_tangents_match():
    start, middle, following = Waypoint(0), Waypoint(4), Waypoint(8)
    middle.transform.location.y = 0.5
    start.next = lambda distance: [middle]
    middle.next = lambda distance: [following]
    with pytest.raises(P7Error, match="CURVATURE"):
        MovingRoute.select(waypoint_map(start), start.transform, 12, 4)


@pytest.mark.parametrize(
    "defect", ["opposite", "sidewalk", "no_permission", "fork", "disconnected"]
)
def test_adjacent_lane_requires_direction_permission_and_unambiguous_connection(defect):
    start = Waypoint(0)
    neighbor = Waypoint(0, -2)
    if defect == "opposite":
        neighbor.lane_id = 2
    elif defect == "sidewalk":
        neighbor.lane_type = carla.LaneType.Sidewalk
    elif defect == "no_permission":
        start.lane_change = carla.LaneChange.NONE
    elif defect == "fork":
        neighbor.next = lambda distance: [
            Waypoint(distance, -2),
            Waypoint(distance, -1),
        ]
    else:
        neighbor.next = lambda distance: []
    start.get_left_lane = lambda: neighbor
    start.get_right_lane = lambda: None
    trace = {}
    with pytest.raises(P7Error, match="NO_CONNECTED_ADJACENT_LANE"):
        MovingRoute.select(
            waypoint_map(start), start.transform, 12, 4, diagnostics=trace
        )
    rejection = trace["adjacent_lane_rejections"][0]
    assert rejection["path"] == "target:left"
    if defect in ("fork", "disconnected"):
        expected = "AMBIGUOUS_FORK" if defect == "fork" else "DISCONNECTED"
        assert rejection["reason"] == f"LANE_UNSUITABLE:{expected}"
        assert rejection["station_m"] == 0


def test_a_full_planar_loop_is_rejected_before_global_projection_becomes_ambiguous():
    start = CircularWaypoint(radius=120)
    with pytest.raises(P7Error, match="SELF_INTERSECTION"):
        MovingRoute.select(waypoint_map(start), start.transform, 2 * pi * 120 + 12, 4)


def test_a_grade_separated_loop_is_not_a_three_dimensional_self_intersection():
    start = CircularWaypoint(radius=120, grade=0.05)
    route = MovingRoute.select(
        waypoint_map(start), start.transform, 2 * pi * 120 + 12, 4
    )
    for station in (2.5, route.stations[-2] - 1):
        assert route.project(route.pose(station).location)[0] == pytest.approx(
            station, abs=5e-5
        )


def test_projection_retains_bounded_endpoint_contract():
    route = MovingRoute.select(Map(), carla.Transform(), 12, 4)
    assert route.project(carla.Location(x=-5)) == pytest.approx((0, 0))
    assert route.project(carla.Location(x=20)) == pytest.approx((12, 0))
    assert route.pose(-5).location.x == 0
    assert route.pose(20).location.x == 12


@pytest.mark.parametrize("defect", ["missing", "non_driving", "heading"])
def test_current_pose_still_requires_a_driving_lane_and_initial_heading(defect):
    start = Waypoint(0)
    if defect == "missing":
        start = None
    elif defect == "non_driving":
        start.lane_type = carla.LaneType.Sidewalk
    pose = carla.Transform(
        rotation=carla.Rotation(yaw=16 if defect == "heading" else 0)
    )
    with pytest.raises(P7Error, match="HEADING" if defect == "heading" else "START"):
        MovingRoute.select(waypoint_map(start), pose, 12, 4)
