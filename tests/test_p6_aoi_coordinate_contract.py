import json
from dataclasses import replace

import pytest

from src.p6.aoi import Aoi, DisplayMapping, Point, Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.config import P6ConfigError
from src.p6.validation.mapping import WindowGeometry, candidate_aoi, to_render_point


def test_archive_rear_point_is_same_candidate_region_in_both_paths():
    layout = load_aoi_layout("config/p6_aoi_candidate.json")
    geometry = WindowGeometry(Rect(-1920, 0, 3840, 1081), Rect(0, 0, 1920, 1080),
                              Rect(320, 180, 1280, 720), (1280, 720), 1, "TEST_ONLY", 96)
    point = Point(0.7445992380, 0.2937976047)
    preview, _ = to_render_point(point, geometry)
    mapping = replace(geometry.display_mapping, display_mapping_verified=True, client_mapping_verified=True)

    result = mapping.to_client(point.x, point.y)

    assert result.x == pytest.approx(1109.63053696)
    assert result.y == pytest.approx(137.301413076)
    assert result == preview
    assert candidate_aoi(result, layout) == "REAR_MIRROR"
    assert replace(layout, mapping=mapping).classify(point.x, point.y) is Aoi.REAR_MIRROR
    assert layout.classify(point.x, point.y) is Aoi.INVALID_LAYOUT


def test_scaled_negative_monitor_client_contract_and_gate():
    geometry = WindowGeometry(Rect(-2560, 0, 4480, 1440), Rect(-2560, 0, 2560, 1440),
                              Rect(-2240, 180, 1920, 1080), (1280, 720), 1, "TEST_ONLY", 144)
    mapping = DisplayMapping(geometry.desktop, geometry.monitor, geometry.client, True, True, surface_size=(1280, 720))

    assert mapping.to_client(0.5, 0.5) == Point(640, 360)
    assert mapping.to_client(0.5, 0.5) == to_render_point(Point(0.5, 0.5), geometry)[0]
    assert replace(mapping, client_mapping_verified=False).to_client(0.5, 0.5) is None


@pytest.mark.parametrize("x,y", [(0.01, 0.01), (1.1, 0.5), (float('nan'), 0.5), (0.5, float('inf'))])
def test_outside_or_nonfinite_gaze_is_missing_never_clamped(x, y):
    mapping = DisplayMapping(Rect(-1920, 0, 3840, 1080), Rect(0, 0, 1920, 1080),
                             Rect(320, 180, 1280, 720), True, True)
    assert mapping.to_client(x, y) is None


def test_legacy_config_defaults_and_optional_surface_scale(tmp_path):
    from pathlib import Path

    raw = json.loads(Path("config/p6_aoi_candidate.json").read_text())
    legacy = load_aoi_layout("config/p6_aoi_candidate.json")
    assert legacy.rear_available is True
    assert legacy.mapping.render_size == (1280, 720)
    raw["surface_size"] = [1280, 720]
    raw["client_rect"] = [0, 0, 1920, 1080]
    raw["display_rect"] = [0, 0, 1920, 1080]
    path = tmp_path / "scaled.json"
    path.write_text(json.dumps(raw))
    layout = load_aoi_layout(path)
    assert layout.mapping.preview_to_client(Point(0.5, 0.5))[0] == Point(640, 360)
    assert not layout.validate().valid
    raw["display_mapping_verified"] = "false"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(P6ConfigError, match="boolean"):
        load_aoi_layout(bad)
