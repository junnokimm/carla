"""Connected, local forward lane corridor, selected from the current ego pose."""

from dataclasses import dataclass
from itertools import pairwise
from math import atan2, cos, degrees, hypot, radians, sin

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


@dataclass
class MovingRoute:
    source: list
    target: list
    stations: list[float]
    side: str

    @classmethod
    def select(cls, world_map, pose, length, step):
        start = world_map.get_waypoint(
            pose.location, project_to_road=False, lane_type=carla.LaneType.Driving
        )
        if start is None or start.is_junction:
            raise P7Error("LANE_UNSUITABLE:START")
        if abs(angle_delta(start.transform.rotation.yaw, pose.rotation.yaw)) > radians(
            15
        ):
            raise P7Error("LANE_UNSUITABLE:HEADING")
        source, stations = [start], [0.0]
        while stations[-1] < length:
            successors = source[-1].next(step)
            if len(successors) != 1 or successors[0].is_junction:
                raise P7Error("LANE_UNSUITABLE:FORK_OR_DISCONNECTED")
            next_wp = successors[0]
            # Curved roads must not silently become straight-line TTC corridors.
            if abs(
                angle_delta(
                    next_wp.transform.rotation.yaw, start.transform.rotation.yaw
                )
            ) > radians(8):
                raise P7Error("LANE_UNSUITABLE:CURVATURE")
            distance = next_wp.transform.location.distance(
                source[-1].transform.location
            )
            if distance < 0.1:
                raise P7Error("LANE_UNSUITABLE:NONADVANCING")
            stations.append(stations[-1] + distance)
            source.append(next_wp)
        for side in ("left", "right"):
            target = [getattr(wp, f"get_{side}_lane")() for wp in source]
            if all(
                neighbor is not None
                and neighbor.lane_type == carla.LaneType.Driving
                and neighbor.lane_id * wp.lane_id > 0
                and not neighbor.is_junction
                and abs(
                    angle_delta(
                        neighbor.transform.rotation.yaw, wp.transform.rotation.yaw
                    )
                )
                < radians(8)
                and str(wp.lane_change) in (side.title(), "Both")
                for wp, neighbor in zip(source, target, strict=True)
            ) and all(
                any(
                    candidate.road_id == b.road_id
                    and candidate.lane_id == b.lane_id
                    and candidate.transform.location.distance(b.transform.location)
                    < step
                    for candidate in a.next(step)
                )
                for a, b in pairwise(target)
            ):
                return cls(source, target, stations, side)
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
        p, q = (
            self.source[index].transform.location,
            self.source[index + 1].transform.location,
        )
        return carla.Transform(
            carla.Location(
                x=a.x + fraction * (b.x - a.x),
                y=a.y + fraction * (b.y - a.y),
                z=a.z + fraction * (b.z - a.z),
            ),
            carla.Rotation(yaw=degrees(atan2(q.y - p.y, q.x - p.x))),
        )

    def project(self, location):
        best = None
        for i in range(len(self.source) - 1):
            a, b = (
                self.source[i].transform.location,
                self.source[i + 1].transform.location,
            )
            dx, dy = b.x - a.x, b.y - a.y
            length = hypot(dx, dy)
            u = max(
                0.0,
                min(
                    1.0,
                    ((location.x - a.x) * dx + (location.y - a.y) * dy)
                    / (length * length),
                ),
            )
            distance = hypot(location.x - a.x - u * dx, location.y - a.y - u * dy)
            candidate = (
                distance,
                self.stations[i] + u * (self.stations[i + 1] - self.stations[i]),
                ((location.y - a.y) * dx - (location.x - a.x) * dy) / length,
            )
            if best is None or candidate[0] < best[0]:
                best = candidate
        return best[1], best[2]
