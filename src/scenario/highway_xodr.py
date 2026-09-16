from __future__ import annotations

from typing import Final

from src.scenario.highway_geometry import (
    DEFAULT_HIGHWAY_SPEC,
    GeometrySegment,
    HighwaySpec,
    Pose,
    advance_pose,
    road_poses,
)

DRIVING_LANE_IDS: Final = (-4, -3, -2, 2, 3, 4)
PRIMARY_LANE_IDS: Final = (-2, -3, -4)
OPPOSITE_LANE_IDS: Final = (2, 3, 4)
MEDIAN_LANE_IDS: Final = (-1, 1)
MEDIAN_LANE_WIDTH: Final = 0.5
LANE_MARK_WIDTH: Final = 0.15


def _number(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def _geometry_xml(start: Pose, segments: tuple[GeometrySegment, ...]) -> str:
    pose = start
    s = 0.0
    fragments: list[str] = []
    for segment in segments:
        attributes = (
            f's="{_number(s)}" x="{_number(pose.x)}" y="{_number(pose.y)}" '
            f'hdg="{_number(pose.heading)}" length="{_number(segment.length)}"'
        )
        match segment.kind:
            case "line":
                child = "<line/>"
            case "arc":
                child = f'<arc curvature="{_number(segment.curvature_start)}"/>'
            case "spiral":
                child = (
                    f'<spiral curvStart="{_number(segment.curvature_start)}" '
                    f'curvEnd="{_number(segment.curvature_end)}"/>'
                )
            case unreachable:
                raise RuntimeError(f"Unsupported geometry kind: {unreachable}")
        fragments.append(f"<geometry {attributes}>{child}</geometry>")
        pose = advance_pose(pose, segment)
        s += segment.length
    return "".join(fragments)


def _lane_xml(lane_id: int, lane_type: str, width: float) -> str:
    outer_lane = abs(lane_id) == 4
    median_lane = abs(lane_id) == 1
    marking = "solid" if outer_lane or median_lane else "broken"
    lane_change = "none" if outer_lane or median_lane else "both"
    return (
        f'<lane id="{lane_id}" type="{lane_type}" level="false">'
        f'<link><predecessor id="{lane_id}"/><successor id="{lane_id}"/></link>'
        f'<width sOffset="0" a="{_number(width)}" b="0" c="0" d="0"/>'
        f'<roadMark sOffset="0" type="{marking}" weight="standard" color="white" '
        f'width="{_number(LANE_MARK_WIDTH)}" laneChange="{lane_change}"/></lane>'
    )


def _lanes_xml(spec: HighwaySpec) -> str:
    left = "".join(
        _lane_xml(
            lane_id,
            "median" if lane_id == 1 else "driving",
            MEDIAN_LANE_WIDTH if lane_id == 1 else spec.lane_width,
        )
        for lane_id in (1, 2, 3, 4)
    )
    right = "".join(
        _lane_xml(
            lane_id,
            "median" if lane_id == -1 else "driving",
            MEDIAN_LANE_WIDTH if lane_id == -1 else spec.lane_width,
        )
        for lane_id in (-1, -2, -3, -4)
    )
    return (
        '<lanes><laneOffset s="0" a="0" b="0" c="0" d="0"/>'
        f'<laneSection s="0"><left>{left}</left>'
        '<center><lane id="0" type="none" level="false"/></center>'
        f"<right>{right}</right></laneSection></lanes>"
    )


def _road_xml(
    index: int, start: Pose, segments: tuple[GeometrySegment, ...], spec: HighwaySpec
) -> str:
    road_id = index + 1
    previous_id = (index - 1) % 4 + 1
    next_id = (index + 1) % 4 + 1
    length = sum(segment.length for segment in segments)
    return (
        f'<road name="Highway loop segment {road_id}" length="{_number(length)}" '
        f'id="{road_id}" junction="-1" rule="RHT">'
        f'<link><predecessor elementType="road" elementId="{previous_id}" contactPoint="end"/>'
        f'<successor elementType="road" elementId="{next_id}" contactPoint="start"/></link>'
        '<type s="0" type="motorway"><speed max="100" unit="km/h"/></type>'
        f"<planView>{_geometry_xml(start, segments)}</planView>"
        '<elevationProfile><elevation s="0" a="0" b="0" c="0" d="0"/></elevationProfile>'
        f"<lateralProfile/>{_lanes_xml(spec)}<objects/><signals/></road>"
    )


def render_xodr(spec: HighwaySpec = DEFAULT_HIGHWAY_SPEC) -> str:
    roads = "".join(
        _road_xml(index, start, segments, spec)
        for index, (start, segments) in enumerate(road_poses(spec))
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenDRIVE><header revMajor="1" revMinor="4" name="highway_loop_3lane" '
        'version="1.00" date="2026-09-15" north="0" south="0" east="0" west="0" '
        'vendor="CARLA Study"/><junctionGroup/>\n'
        f"{roads}</OpenDRIVE>\n"
    )
