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


def to_render_point(normalized: Point, observed: WindowGeometry, *,
                    logical_size: tuple[float, float] | None = None) -> tuple[Point | None, str | None]:
    point, reason = observed.display_mapping.preview_to_client(normalized)
    if point is None or logical_size is None:
        return point, reason
    # Undo the same integer-rounded aspect fit used by DriverViewPresentation.
    # Windows/DPI coordinates above belong to the physical display surface;
    # AOI rectangles below belong to the unchanged logical driving canvas.
    sw, sh = observed.surface_size
    lw, lh = logical_size
    if sw * lh <= sh * lw:
        width, height = sw, sw * int(lh) // int(lw)
    else:
        width, height = sh * int(lw) // int(lh), sh
    viewport = Rect((sw - width) // 2, (sh - height) // 2, width, height)
    if not viewport.contains(point):
        return None, "OUTSIDE_VIEWPORT"
    return Point((point.x - viewport.x) * lw / width,
                 (point.y - viewport.y) * lh / height), None


def candidate_aoi(point: Point, layout: AoiLayout) -> str:
    if layout.validate().issues:
        return "INVALID_LAYOUT"
    for name, region in (("REAR_MIRROR", layout.rear), ("LEFT_MIRROR", layout.left),
                         ("RIGHT_MIRROR", layout.right), ("FORWARD", layout.forward)):
        if region.contains(point):
            return name
    return "NO_AOI"
