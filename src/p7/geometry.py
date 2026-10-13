from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin

from src.experiment.lane_geometry import PlanarPose


def station(pose: PlanarPose, origin: PlanarPose) -> float:
    return (pose.x - origin.x) * cos(origin.yaw_rad) + (pose.y - origin.y) * sin(origin.yaw_rad)


def smooth_lane_change(elapsed_s: float, duration_s: float) -> tuple[float, float]:
    u = min(1.0, max(0.0, elapsed_s / duration_s))
    return 6 * u**5 - 15 * u**4 + 10 * u**3, (30 * u**4 - 60 * u**3 + 30 * u**2) / duration_s


@dataclass(frozen=True, slots=True)
class ProjectionBox:
    left: float
    bottom: float
    right: float
    top: float

    def contains(self, other: ProjectionBox) -> bool:
        return (self.left <= other.left and self.right >= other.right
                and self.bottom <= other.bottom and self.top >= other.top)

    def disjoint(self, other: ProjectionBox) -> bool:
        return (self.right <= other.left or self.left >= other.right
                or self.top <= other.bottom or self.bottom >= other.top)
