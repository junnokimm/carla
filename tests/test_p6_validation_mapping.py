from dataclasses import replace

import pytest

from src.p6.aoi import Aoi, Point, Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.validation.mapping import WindowGeometry, candidate_aoi, to_render_point


def geometry() -> WindowGeometry:
    return WindowGeometry(
        desktop=Rect(-1920, 0, 4480, 1440), monitor=Rect(0, 0, 2560, 1440),
        client=Rect(320, 180, 1920, 1080), surface_size=(1280, 720),
        hwnd=1, monitor_name="FAKE_DISPLAY", dpi=144,
    )


def test_normalized_display_is_not_normalized_virtual_desktop() -> None:
    observed = geometry()

    point, reason = to_render_point(Point(0.5, 0.5), observed)

    assert point == Point(640, 360)
    assert reason is None
    mapping = observed.display_mapping
    assert mapping.desktop.x == -1920
    assert not mapping.display_mapping_verified
    assert not mapping.client_mapping_verified


@pytest.mark.parametrize("normalized, expected", [
    (Point(0.125, 0.125), Point(0, 0)),
    (Point(0.875 - 1 / 2560, 0.875 - 1 / 1440), Point(1279 + 1 / 3, 719 + 1 / 3)),
])
def test_client_offset_and_physical_to_surface_scaling(normalized, expected) -> None:
    point, reason = to_render_point(normalized, geometry())

    assert point.x == pytest.approx(expected.x)
    assert point.y == pytest.approx(expected.y)
    assert reason is None


@pytest.mark.parametrize("point,reason", [
    (Point(0.01, 0.01), "OUTSIDE_CLIENT"),
    (Point(1.1, 0.5), "OUTSIDE_DISPLAY"),
    (Point(float("nan"), 0.5), "NONFINITE"),
])
def test_outside_and_invalid_coordinates_are_not_clamped(point, reason) -> None:
    assert to_render_point(point, geometry()) == (None, reason)


def test_negative_monitor_origin_is_supported() -> None:
    observed = replace(geometry(), monitor=Rect(-1920, 0, 1920, 1080),
                       client=Rect(-1600, 180, 1280, 720), dpi=96)

    assert to_render_point(Point(0.5, 0.5), observed) == (Point(640, 360), None)


@pytest.mark.parametrize("point,expected", [
    (Point(200, 200), "FORWARD"), (Point(1000, 120), "REAR_MIRROR"),
    (Point(100, 520), "LEFT_MIRROR"), (Point(1100, 520), "RIGHT_MIRROR"),
    (Point(700, 600), "NO_AOI"),
])
def test_candidate_classification_never_approves_production(point, expected) -> None:
    layout = load_aoi_layout("config/p6_aoi_candidate.json")

    assert candidate_aoi(point, layout) == expected
    assert layout.classify(0.5, 0.5) is Aoi.INVALID_LAYOUT
    assert not layout.mapping.display_mapping_verified
    assert layout.validate().tablet_status == "NOT_TESTED"
