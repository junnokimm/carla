from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class HighwaySpec:
    straight_length: float = 3000.0
    radius: float = 1000.0
    transition_length: float = 60.0
    lane_width: float = 3.5
    lane_ids: tuple[int, int, int] = (-1, -2, -3)


@dataclass(frozen=True, slots=True)
class Pose:
    x: float
    y: float
    heading: float


@dataclass(frozen=True, slots=True)
class GeometrySegment:
    kind: str
    length: float
    curvature_start: float = 0.0
    curvature_end: float = 0.0


@dataclass(frozen=True, slots=True)
class GeometryValidation:
    straight_lengths: tuple[float, float]
    transition_length: float
    constant_radius: float
    constant_arc_angle: float
    constant_arc_length: float
    total_length: float
    start_pose: Pose
    end_pose: Pose
    position_error: float
    heading_error: float


DEFAULT_HIGHWAY_SPEC: Final = HighwaySpec()


def constant_arc_angle(spec: HighwaySpec) -> float:
    """Return the constant-curvature angle after two Euler transitions."""
    return math.pi - spec.transition_length / spec.radius


def constant_arc_length(spec: HighwaySpec) -> float:
    """Return the length of one constant-radius semicircle portion."""
    return spec.radius * constant_arc_angle(spec)


def _road_segments(spec: HighwaySpec) -> tuple[tuple[GeometrySegment, ...], ...]:
    half_arc = constant_arc_length(spec) / 2.0
    curvature = 1.0 / spec.radius
    enter = GeometrySegment("spiral", spec.transition_length, 0.0, curvature)
    exit = GeometrySegment("spiral", spec.transition_length, curvature, 0.0)
    arc = GeometrySegment("arc", half_arc, curvature, curvature)
    return (
        (GeometrySegment("line", spec.straight_length), enter, arc),
        (arc, exit, GeometrySegment("line", spec.straight_length)),
        (enter, arc),
        (arc, exit),
    )


def road_lengths(spec: HighwaySpec = DEFAULT_HIGHWAY_SPEC) -> tuple[float, ...]:
    return tuple(
        sum(segment.length for segment in road) for road in _road_segments(spec)
    )


def advance_pose(pose: Pose, segment: GeometrySegment) -> Pose:
    match segment.kind:
        case "line":
            return Pose(
                pose.x + segment.length * math.cos(pose.heading),
                pose.y + segment.length * math.sin(pose.heading),
                pose.heading,
            )
        case "arc":
            curvature = segment.curvature_start
            final_heading = pose.heading + curvature * segment.length
            return Pose(
                pose.x + (math.sin(final_heading) - math.sin(pose.heading)) / curvature,
                pose.y + (math.cos(pose.heading) - math.cos(final_heading)) / curvature,
                final_heading,
            )
        case "spiral":
            samples = 4096
            step = segment.length / samples
            curvature_rate = (
                segment.curvature_end - segment.curvature_start
            ) / segment.length
            x = pose.x
            y = pose.y
            for index in range(samples):
                distance = (index + 0.5) * step
                heading = (
                    pose.heading
                    + segment.curvature_start * distance
                    + 0.5 * curvature_rate * distance**2
                )
                x += step * math.cos(heading)
                y += step * math.sin(heading)
            return Pose(
                x,
                y,
                pose.heading
                + segment.curvature_start * segment.length
                + 0.5 * curvature_rate * segment.length**2,
            )
        case unreachable:
            raise RuntimeError(f"Unsupported geometry kind: {unreachable}")


def road_poses(
    spec: HighwaySpec,
) -> tuple[tuple[Pose, tuple[GeometrySegment, ...]], ...]:
    pose = Pose(0.0, 0.0, 0.0)
    roads: list[tuple[Pose, tuple[GeometrySegment, ...]]] = []
    for segments in _road_segments(spec):
        roads.append((pose, segments))
        for segment in segments:
            pose = advance_pose(pose, segment)
    return tuple(roads)


def validate_geometry(spec: HighwaySpec) -> GeometryValidation:
    """Numerically prove the line-spiral-arc loop closes in OpenDRIVE coordinates."""
    roads = road_poses(spec)
    start_pose = roads[0][0]
    end_pose = start_pose
    for _, segments in roads:
        for segment in segments:
            end_pose = advance_pose(end_pose, segment)
    heading_error = abs(
        math.atan2(
            math.sin(end_pose.heading - start_pose.heading),
            math.cos(end_pose.heading - start_pose.heading),
        )
    )
    return GeometryValidation(
        straight_lengths=(spec.straight_length, spec.straight_length),
        transition_length=spec.transition_length,
        constant_radius=spec.radius,
        constant_arc_angle=constant_arc_angle(spec),
        constant_arc_length=constant_arc_length(spec),
        total_length=sum(
            segment.length for road in _road_segments(spec) for segment in road
        ),
        start_pose=start_pose,
        end_pose=end_pose,
        position_error=math.hypot(end_pose.x - start_pose.x, end_pose.y - start_pose.y),
        heading_error=heading_error,
    )


def build_xodr(spec: HighwaySpec = DEFAULT_HIGHWAY_SPEC) -> str:
    from src.scenario.highway_xodr import render_xodr

    return render_xodr(spec)
