"""Connected, local forward lane corridor, selected from the current ego pose."""

from dataclasses import dataclass
from itertools import pairwise
from math import atan2, cos, degrees, hypot, isfinite, radians, sin

import carla

from src.p7.types import P7Error


def pose_record(pose):
    return {
        "x": pose.location.x,
        "y": pose.location.y,
        "z": pose.location.z,
        "yaw": pose.rotation.yaw,
        "pitch": pose.rotation.pitch,
        "roll": pose.rotation.roll,
    }


def box_record(box):
    return {
        "extent": {axis: getattr(box.extent, axis) for axis in ("x", "y", "z")},
        "location": {axis: getattr(box.location, axis) for axis in ("x", "y", "z")},
    }


def angle_delta(a, b):
    return atan2(sin(radians(a - b)), cos(radians(a - b)))


def _xyz(point):
    return point.x, point.y, point.z


def _difference(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _same_direction(a, b):
    return _dot(a, b) >= cos(radians(8)) * hypot(*a) * hypot(*b)


def _waypoint_record(wp):
    return {
        "road_id": wp.road_id,
        "section_id": wp.section_id,
        "lane_id": wp.lane_id,
        "lane_type": str(wp.lane_type),
        "is_junction": wp.is_junction,
        "pose": pose_record(wp.transform),
    }


def _waypoint_key(wp):
    # CARLA waypoint IDs are quantized. Do not collapse distinct positions or
    # different road continuations merely because their IDs/poses overlap.
    return (
        wp.road_id,
        wp.section_id,
        wp.lane_id,
        *pose_record(wp.transform).values(),
    )


def _check_geometry(wp):
    if not all(isfinite(value) for value in pose_record(wp.transform).values()):
        raise P7Error("LANE_UNSUITABLE:INVALID_GEOMETRY")


def _checked_segment(a, b, previous=None):
    _check_geometry(a)
    _check_geometry(b)
    vector = _difference(_xyz(b.transform.location), _xyz(a.transform.location))
    distance = hypot(*vector)
    forward_a = _xyz(a.transform.get_forward_vector())
    forward_b = _xyz(b.transform.get_forward_vector())
    if distance < 0.1 or hypot(*vector[:2]) < 0.1 or _dot(forward_a, vector) <= 0:
        raise P7Error("LANE_UNSUITABLE:NONADVANCING")
    # Bound local tangent/segment changes, not the total heading accumulated
    # along a connected curve. TTC continues to use this polyline's stations.
    if (
        not _same_direction(forward_a, forward_b)
        or not _same_direction(forward_a, vector)
        or not _same_direction(vector, forward_b)
        or (previous is not None and not _same_direction(previous, vector))
    ):
        raise P7Error("LANE_UNSUITABLE:CURVATURE")
    return distance, vector


def _segments_intersect(a, b, c, d):
    # A 3D check permits grade-separated crossings while rejecting a loop that
    # would make global nearest-point station projection ambiguous.
    tolerance = 1e-6
    if any(
        max(min(a[i], b[i]), min(c[i], d[i]))
        > min(max(a[i], b[i]), max(c[i], d[i])) + tolerance
        for i in range(3)
    ):
        return False
    ab, cd, ac = _difference(b, a), _difference(d, c), _difference(a, c)
    aa, bb, cc = _dot(ab, ab), _dot(ab, cd), _dot(cd, cd)
    dd, ee = _dot(ab, ac), _dot(cd, ac)
    pairs = [
        (0.0, max(0.0, min(1.0, ee / cc))),
        (1.0, max(0.0, min(1.0, (ee + bb) / cc))),
        (max(0.0, min(1.0, -dd / aa)), 0.0),
        (max(0.0, min(1.0, (bb - dd) / aa)), 1.0),
    ]
    determinant = aa * cc - bb * bb
    if determinant > 1e-12 * aa * cc:
        u, v = (bb * ee - cc * dd) / determinant, (aa * ee - bb * dd) / determinant
        if 0 <= u <= 1 and 0 <= v <= 1:
            pairs.append((u, v))
    return any(
        hypot(*(ac[i] + u * ab[i] - v * cd[i] for i in range(3))) <= tolerance
        for u, v in pairs
    )


def _check_simple_extension(path, wp):
    for a, b in pairwise(path[:-1]):
        if _segments_intersect(a, b, path[-1], wp):
            raise P7Error("LANE_UNSUITABLE:SELF_INTERSECTION")


def _next_step(wp, step, previous, station, path, diagnostics):
    raw = list(wp.next(step))
    unique = {
        _waypoint_key(candidate): candidate
        for candidate in raw
        if candidate.lane_type == carla.LaneType.Driving
    }
    try:
        if not unique:
            raise P7Error("LANE_UNSUITABLE:DISCONNECTED")
        if len(unique) > 1:
            raise P7Error("LANE_UNSUITABLE:AMBIGUOUS_FORK")
        following = next(iter(unique.values()))
        distance, vector = _checked_segment(wp, following, previous)
    except P7Error as exc:
        if diagnostics is not None:
            diagnostics["route_selection_failure"] = {
                "reason": str(exc),
                "path": path,
                "station_m": station,
                "waypoint": _waypoint_record(wp),
                "raw_successor_count": len(raw),
                "unique_driving_successor_count": len(unique),
                "candidates": [
                    {
                        **_waypoint_record(candidate),
                        "local_heading_delta_deg": degrees(
                            angle_delta(
                                candidate.transform.rotation.yaw,
                                wp.transform.rotation.yaw,
                            )
                        ),
                        "distance_m": candidate.transform.location.distance(
                            wp.transform.location
                        ),
                    }
                    for candidate in raw
                ],
            }
        raise
    return following, distance, vector


@dataclass
class MovingRoute:
    source: list
    target: list
    stations: list[float]
    side: str

    @classmethod
    def select(cls, world_map, pose, length, step, *, diagnostics=None, adjacent=True):
        if not all(isfinite(value) and value > 0 for value in (length, step)):
            raise P7Error("LANE_UNSUITABLE:INVALID_ROUTE_REQUEST")
        if diagnostics is not None:
            diagnostics.pop("route_selection_failure", None)
            diagnostics.pop("adjacent_lane_rejections", None)
        start = world_map.get_waypoint(
            pose.location, project_to_road=False, lane_type=carla.LaneType.Driving
        )
        if diagnostics is not None:
            diagnostics["route_start"] = (
                None if start is None else _waypoint_record(start)
            )
        if start is None or start.lane_type != carla.LaneType.Driving:
            raise P7Error("LANE_UNSUITABLE:START")
        _check_geometry(start)
        if abs(angle_delta(start.transform.rotation.yaw, pose.rotation.yaw)) > radians(
            15
        ):
            raise P7Error("LANE_UNSUITABLE:HEADING")
        source, stations = [start], [0.0]
        # Snapshot geometry once; the self-intersection check must not keep
        # asking CARLA's Python bindings for every earlier waypoint transform.
        source_points = [_xyz(start.transform.location)]
        previous = None
        while stations[-1] < length:
            next_wp, distance, vector = _next_step(
                source[-1], step, previous, stations[-1], "source", diagnostics
            )
            next_point = _xyz(next_wp.transform.location)
            try:
                _check_simple_extension(source_points, next_point)
            except P7Error as exc:
                if diagnostics is not None:
                    diagnostics["route_selection_failure"] = {
                        "reason": str(exc),
                        "path": "source",
                        "station_m": stations[-1],
                        "waypoint": _waypoint_record(next_wp),
                    }
                raise
            stations.append(stations[-1] + distance)
            source.append(next_wp)
            source_points.append(next_point)
            previous = vector
        if not adjacent:
            return cls(source, source, stations, "current")
        rejections = []
        for side in ("left", "right"):
            target, trace = [], {}
            try:
                for station, wp in zip(stations, source, strict=True):
                    neighbor = getattr(wp, f"get_{side}_lane")()
                    if (
                        neighbor is None
                        or neighbor.lane_type != carla.LaneType.Driving
                        or neighbor.lane_id * wp.lane_id <= 0
                        or not _same_direction(
                            _xyz(wp.transform.get_forward_vector()),
                            _xyz(neighbor.transform.get_forward_vector()),
                        )
                        or str(wp.lane_change) not in (side.title(), "Both")
                        or _waypoint_key(neighbor) == _waypoint_key(wp)
                    ):
                        trace["route_selection_failure"] = {
                            "reason": "LANE_UNSUITABLE:ADJACENT_LANE",
                            "path": f"target:{side}",
                            "station_m": station,
                            "waypoint": _waypoint_record(wp),
                            "neighbor": (
                                None if neighbor is None else _waypoint_record(neighbor)
                            ),
                        }
                        raise P7Error("LANE_UNSUITABLE:ADJACENT_LANE")
                    _check_geometry(neighbor)
                    target.append(neighbor)
                previous = None
                target_points = [_xyz(wp.transform.location) for wp in target]
                for i, (a, b) in enumerate(pairwise(target)):
                    following, _, _ = _next_step(
                        a, step, previous, stations[i], f"target:{side}", trace
                    )
                    if (
                        following.road_id != b.road_id
                        or following.lane_id != b.lane_id
                        or following.transform.location.distance(b.transform.location)
                        >= step
                    ):
                        raise P7Error("LANE_UNSUITABLE:ADJACENT_DISCONNECTED")
                    _, previous = _checked_segment(a, b, previous)
                    _check_simple_extension(
                        target_points[: i + 1], target_points[i + 1]
                    )
            except P7Error as exc:
                rejections.append(
                    trace.get(
                        "route_selection_failure",
                        {"reason": str(exc), "path": f"target:{side}"},
                    )
                )
            else:
                if diagnostics is not None:
                    diagnostics["route_selected_side"] = side
                    if rejections:
                        diagnostics["adjacent_lane_rejections"] = rejections
                return cls(source, target, stations, side)
        if diagnostics is not None:
            diagnostics["adjacent_lane_rejections"] = rejections
        raise P7Error("LANE_UNSUITABLE:NO_CONNECTED_ADJACENT_LANE")

    def pose(self, station, fraction=0.0):
        index = min(
            len(self.stations) - 2,
            next(
                (
                    i
                    for i in range(len(self.stations) - 1)
                    if station <= self.stations[i + 1]
                ),
                len(self.stations) - 2,
            ),
        )
        u = max(
            0.0,
            min(
                1.0,
                (station - self.stations[index])
                / (self.stations[index + 1] - self.stations[index]),
            ),
        )

        def point(path):
            a, b = path[index].transform.location, path[index + 1].transform.location
            return carla.Location(
                x=a.x + u * (b.x - a.x),
                y=a.y + u * (b.y - a.y),
                z=a.z + u * (b.z - a.z),
            )

        a, b = point(self.source), point(self.target)
        source_vector = _difference(
            _xyz(self.source[index + 1].transform.location),
            _xyz(self.source[index].transform.location),
        )
        target_vector = _difference(
            _xyz(self.target[index + 1].transform.location),
            _xyz(self.target[index].transform.location),
        )
        dx, dy, dz = (
            p + fraction * (q - p)
            for p, q in zip(source_vector, target_vector, strict=True)
        )

        def bank(path):
            a, b = path[index].transform.rotation, path[index + 1].transform.rotation
            return a.roll + u * degrees(angle_delta(b.roll, a.roll))

        source_bank, target_bank = bank(self.source), bank(self.target)
        return carla.Transform(
            carla.Location(
                x=a.x + fraction * (b.x - a.x),
                y=a.y + fraction * (b.y - a.y),
                z=a.z + fraction * (b.z - a.z),
            ),
            carla.Rotation(
                yaw=degrees(atan2(dy, dx)),
                pitch=degrees(atan2(dz, hypot(dx, dy))),
                roll=source_bank
                + fraction * degrees(angle_delta(target_bank, source_bank)),
            ),
        )

    def project(self, location):
        best = None
        for i in range(len(self.source) - 1):
            a, b = (
                self.source[i].transform.location,
                self.source[i + 1].transform.location,
            )
            dx, dy, dz = b.x - a.x, b.y - a.y, b.z - a.z
            length_squared = dx * dx + dy * dy + dz * dz
            u = max(
                0.0,
                min(
                    1.0,
                    (
                        (location.x - a.x) * dx
                        + (location.y - a.y) * dy
                        + (location.z - a.z) * dz
                    )
                    / length_squared,
                ),
            )
            distance = hypot(
                location.x - a.x - u * dx,
                location.y - a.y - u * dy,
                location.z - a.z - u * dz,
            )
            candidate = (
                distance,
                self.stations[i] + u * (self.stations[i + 1] - self.stations[i]),
                ((location.y - a.y) * dx - (location.x - a.x) * dy) / hypot(dx, dy),
            )
            if best is None or candidate[0] < best[0]:
                best = candidate
        return best[1], best[2]
