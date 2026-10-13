from __future__ import annotations

from dataclasses import dataclass

from src.p6.aoi import AoiLayout, DisplayMapping, Point, Rect


@dataclass(frozen=True, slots=True)
class ValidationError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    desktop: Rect
    monitor: Rect
    client: Rect
    surface_size: tuple[int, int]
    hwnd: int
    monitor_name: str
    dpi: int

    def __post_init__(self) -> None:
        if any(rect.width <= 0 or rect.height <= 0
               for rect in (self.desktop, self.monitor, self.client)):
            raise ValidationError("Empty Windows geometry")
        if min(self.surface_size) <= 0 or not self.client.within(self.monitor):
            raise ValidationError("Client must fit entirely within one monitor")

    @property
    def display_mapping(self) -> DisplayMapping:
        return DisplayMapping(self.desktop, self.monitor, self.client, False, False, self.surface_size)


def to_render_point(normalized: Point, observed: WindowGeometry) -> tuple[Point | None, str | None]:
    return observed.display_mapping.preview_to_client(normalized)


def candidate_aoi(point: Point, layout: AoiLayout) -> str:
    if layout.validate().issues:
        return "INVALID_LAYOUT"
    for name, region in (("REAR_MIRROR", layout.rear), ("LEFT_MIRROR", layout.left),
                         ("RIGHT_MIRROR", layout.right), ("FORWARD", layout.forward)):
        if region.contains(point):
            return name
    return "NO_AOI"
