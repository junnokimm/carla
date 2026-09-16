from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

import pytest


def test_stadium_geometry_has_required_lengths_and_closed_pose() -> None:
    from src.scenario.highway_geometry import DEFAULT_HIGHWAY_SPEC, validate_geometry

    report = validate_geometry(DEFAULT_HIGHWAY_SPEC)

    assert report.straight_lengths == (3000.0, 3000.0)
    assert report.transition_length == 60.0
    assert report.constant_radius == 1000.0
    assert report.constant_arc_angle == 3.081592653589793
    assert report.constant_arc_length == 3081.592653589793
    assert report.total_length == pytest.approx(12403.185307179586)
    assert report.position_error < 1e-6
    assert report.heading_error < 1e-12


def test_generated_xodr_has_four_road_cycle_and_bidirectional_lanes() -> None:
    from src.scenario.highway_geometry import DEFAULT_HIGHWAY_SPEC, build_xodr

    root = ElementTree.fromstring(build_xodr(DEFAULT_HIGHWAY_SPEC))
    roads = root.findall("road")

    assert len(roads) == 4
    assert all(road.attrib["junction"] == "-1" for road in roads)
    assert all(
        road.find("type/speed").attrib == {"max": "100", "unit": "km/h"}
        for road in roads
    )
    for index, road in enumerate(roads, start=1):
        successor = road.find("link/successor")
        assert successor is not None
        assert successor.attrib["elementId"] == str(index % 4 + 1)
        left_lanes = road.findall("lanes/laneSection/left/lane")
        right_lanes = road.findall("lanes/laneSection/right/lane")
        assert [lane.attrib["id"] for lane in left_lanes] == ["1", "2", "3", "4"]
        assert [lane.attrib["id"] for lane in right_lanes] == ["-1", "-2", "-3", "-4"]
        assert road.find("lanes/laneSection/center/lane").attrib == {
            "id": "0",
            "type": "none",
            "level": "false",
        }

        driving_lanes = [
            lane
            for lane in (*left_lanes, *right_lanes)
            if lane.attrib["type"] == "driving"
        ]
        assert [lane.attrib["id"] for lane in driving_lanes] == [
            "2",
            "3",
            "4",
            "-2",
            "-3",
            "-4",
        ]
        assert all(lane.find("width").attrib["a"] == "3.5" for lane in driving_lanes)

        median_lanes = [
            lane
            for lane in (*left_lanes, *right_lanes)
            if lane.attrib["type"] == "median"
        ]
        assert [lane.attrib["id"] for lane in median_lanes] == ["1", "-1"]
        assert all(lane.find("width").attrib["a"] == "0.5" for lane in median_lanes)

        markings = {
            lane.attrib["id"]: lane.find("roadMark").attrib
            for lane in (*left_lanes, *right_lanes)
        }
        assert markings == {
            "1": {
                "sOffset": "0",
                "type": "solid",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "none",
            },
            "2": {
                "sOffset": "0",
                "type": "broken",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "both",
            },
            "3": {
                "sOffset": "0",
                "type": "broken",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "both",
            },
            "4": {
                "sOffset": "0",
                "type": "solid",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "none",
            },
            "-1": {
                "sOffset": "0",
                "type": "solid",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "none",
            },
            "-2": {
                "sOffset": "0",
                "type": "broken",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "both",
            },
            "-3": {
                "sOffset": "0",
                "type": "broken",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "both",
            },
            "-4": {
                "sOffset": "0",
                "type": "solid",
                "weight": "standard",
                "color": "white",
                "width": "0.15",
                "laneChange": "none",
            },
        }


def test_generated_map_matches_deterministic_generator() -> None:
    from src.scenario.highway_geometry import DEFAULT_HIGHWAY_SPEC, build_xodr

    map_path = Path(__file__).parents[1] / "maps" / "highway_loop_3lane.xodr"

    assert map_path.read_text(encoding="utf-8") == build_xodr(DEFAULT_HIGHWAY_SPEC)
