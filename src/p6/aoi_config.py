from __future__ import annotations

import json
from math import isfinite
from pathlib import Path

from src.p6.aoi import AoiLayout, DisplayMapping, Point, Polygon, Rect
from src.p6.config import P6ConfigError


def _rect(value: list[int | float]) -> Rect:
    if len(value) != 4:
        raise P6ConfigError("AOI rectangles require x, y, width, height")
    return Rect(*(float(item) for item in value))


def _polygon(value: list[list[int | float]]) -> Polygon:
    if len(value) < 3 or any(len(point) != 2 for point in value):
        raise P6ConfigError("AOI polygons require at least three x/y points")
    return Polygon(tuple(Point(float(point[0]), float(point[1])) for point in value))


def _verified(value: bool, field: str) -> bool:
    if type(value) is not bool:
        raise P6ConfigError(f"{field} must be a boolean, not a truthy value")
    return value


def load_aoi_layout(path: str | Path) -> AoiLayout:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise P6ConfigError(f"cannot read AOI config: {error}") from error
    if not isinstance(raw, dict):
        raise P6ConfigError("AOI config must be a JSON object")
    required = {
        "version",
        "desktop_rect",
        "display_rect",
        "client_rect",
        "display_mapping_verified",
        "client_mapping_verified",
        "forward_polygon",
        "rear_rect",
        "left_rect",
        "right_rect",
        "tablet_rect",
        "tablet_mapping_verified",
        "other_polygons",
    }
    unknown = sorted(set(raw) - required - {"surface_size", "rear_available"})
    missing = sorted(required - set(raw))
    if unknown or missing:
        raise P6ConfigError(
            f"AOI fields missing={','.join(missing)} unknown={','.join(unknown)}"
        )
    try:
        tablet_value = raw["tablet_rect"]
        tablet = None if tablet_value is None else _rect(tablet_value)
        surface = raw.get("surface_size")
        if surface is not None and (not isinstance(surface, list) or len(surface) != 2 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
            for value in surface
        )):
            raise P6ConfigError("surface_size requires two finite positive render dimensions")
        mapping = DisplayMapping(
            desktop=_rect(raw["desktop_rect"]),
            display=_rect(raw["display_rect"]),
            client=_rect(raw["client_rect"]),
            display_mapping_verified=_verified(raw["display_mapping_verified"], "display_mapping_verified"),
            client_mapping_verified=_verified(raw["client_mapping_verified"], "client_mapping_verified"),
            surface_size=None if surface is None else (float(surface[0]), float(surface[1])),
        )
        return AoiLayout(
            version=str(raw["version"]),
            mapping=mapping,
            forward=_polygon(raw["forward_polygon"]),
            rear=_rect(raw["rear_rect"]),
            left=_rect(raw["left_rect"]),
            right=_rect(raw["right_rect"]),
            tablet=tablet,
            other=tuple(_polygon(value) for value in raw["other_polygons"]),
            tablet_mapping_verified=_verified(raw["tablet_mapping_verified"], "tablet_mapping_verified"),
            rear_available=_verified(raw.get("rear_available", True), "rear_available"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise P6ConfigError(f"invalid AOI config value: {error}") from error
