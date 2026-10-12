from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class Aoi(StrEnum):
    FORWARD = "FORWARD"
    REAR_MIRROR = "REAR_MIRROR"
    LEFT_MIRROR = "LEFT_MIRROR"
    RIGHT_MIRROR = "RIGHT_MIRROR"
    TABLET = "TABLET"
    OTHER = "OTHER"
    NO_AOI = "NO_AOI"
    INVALID_LAYOUT = "INVALID_LAYOUT"


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def contains(self, point: Point) -> bool:
        return (
            self.x <= point.x < self.x + self.width
            and self.y <= point.y < self.y + self.height
        )

    def within(self, outer: Rect) -> bool:
        return (
            self.x >= outer.x
            and self.y >= outer.y
            and self.x + self.width <= outer.x + outer.width
            and self.y + self.height <= outer.y + outer.height
        )

    def intersects(self, other: Rect) -> bool:
        return (
            self.x < other.x + other.width
            and self.x + self.width > other.x
            and self.y < other.y + other.height
            and self.y + self.height > other.y
        )


@dataclass(frozen=True, slots=True)
class _UnavailableRect(Rect):
    def contains(self, point: Point) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Polygon:
    points: tuple[Point, ...]

    def contains(self, point: Point) -> bool:
        inside = False
        previous = self.points[-1]
        for current in self.points:
            crosses = (current.y > point.y) != (previous.y > point.y)
            if crosses:
                boundary_x = (previous.x - current.x) * (point.y - current.y) / (
                    previous.y - current.y
                ) + current.x
                if point.x < boundary_x:
                    inside = not inside
            previous = current
        return inside


@dataclass(frozen=True, slots=True)
class DisplayMapping:
    desktop: Rect
    display: Rect
    client: Rect
    display_mapping_verified: bool
    client_mapping_verified: bool
    surface_size: tuple[float, float] | None = None

    @property
    def render_size(self) -> tuple[float, float]:
        return self.surface_size or (self.client.width, self.client.height)

    @property
    def geometry_valid(self) -> bool:
        return (all(isfinite(value) for rect in (self.desktop, self.display, self.client)
                    for value in (rect.x, rect.y, rect.width, rect.height))
                and all(rect.width > 0 and rect.height > 0 for rect in (self.desktop, self.display, self.client))
                and all(isfinite(value) and value > 0 for value in self.render_size)
                and self.client.within(self.display))

    def preview_to_client(self, point: Point) -> tuple[Point | None, str | None]:
        if not self.geometry_valid:
            return None, "INVALID_GEOMETRY"
        if not isfinite(point.x) or not isfinite(point.y):
            return None, "NONFINITE"
        if not (0 <= point.x < 1 and 0 <= point.y < 1):
            return None, "OUTSIDE_DISPLAY"
        screen = Point(self.display.x + point.x * self.display.width,
                       self.display.y + point.y * self.display.height)
        if not self.client.contains(screen):
            return None, "OUTSIDE_CLIENT"
        width, height = self.render_size
        return Point((screen.x - self.client.x) * width / self.client.width,
                     (screen.y - self.client.y) * height / self.client.height), None

    def to_client(self, normalized_x: float, normalized_y: float) -> Point | None:
        if not self.display_mapping_verified or not self.client_mapping_verified:
            return None
        return self.preview_to_client(Point(normalized_x, normalized_y))[0]


@dataclass(frozen=True, slots=True)
class LayoutReport:
    valid: bool
    issues: tuple[str, ...]
    tablet_status: str


@dataclass(frozen=True, slots=True)
class AoiLayout:
    version: str
    mapping: DisplayMapping
    forward: Polygon
    rear: Rect
    left: Rect
    right: Rect
    tablet: Rect | None
    other: tuple[Polygon, ...]
    tablet_mapping_verified: bool
    rear_available: bool = True

    def __post_init__(self) -> None:
        if not self.rear_available:
            object.__setattr__(
                self,
                "rear",
                _UnavailableRect(
                    self.rear.x,
                    self.rear.y,
                    self.rear.width,
                    self.rear.height,
                ),
            )

    def validate(self) -> LayoutReport:
        bounds = Rect(0, 0, *self.mapping.render_size)
        named = (
            (("rear", self.rear),) if self.rear_available else ()
        ) + (("left", self.left), ("right", self.right))
        issues = [f"{name}:clipped" for name, rect in named if not rect.within(bounds)]
        rectangles = named + (() if self.tablet is None else (("tablet", self.tablet),))
        for index, (name, rect) in enumerate(rectangles):
            issues.extend(
                f"{name}:{other_name}:overlap"
                for other_name, other in rectangles[index + 1 :]
                if rect.intersects(other)
            )
        for name, rect in rectangles:
            rect_points = (
                Point(rect.x, rect.y),
                Point(rect.x + rect.width, rect.y),
                Point(rect.x, rect.y + rect.height),
                Point(rect.x + rect.width, rect.y + rect.height),
            )
            if any(self.forward.contains(point) for point in rect_points) or any(
                rect.contains(point) for point in self.forward.points
            ):
                issues.append(f"forward:{name}:overlap")
        tablet_status = (
            "VALIDATED"
            if self.tablet is not None and self.tablet_mapping_verified
            else "NOT_TESTED"
        )
        mapping_valid = (
            self.mapping.display_mapping_verified
            and self.mapping.client_mapping_verified
            and self.mapping.geometry_valid
        )
        return LayoutReport(mapping_valid and not issues, tuple(issues), tablet_status)

    def classify(self, normalized_x: float, normalized_y: float) -> Aoi:
        if not self.validate().valid:
            return Aoi.INVALID_LAYOUT
        point = self.mapping.to_client(normalized_x, normalized_y)
        if point is None:
            return Aoi.NO_AOI
        regions = (
            ((Aoi.REAR_MIRROR, self.rear),) if self.rear_available else ()
        ) + (
            (Aoi.LEFT_MIRROR, self.left),
            (Aoi.RIGHT_MIRROR, self.right),
        )
        for aoi, rect in regions:
            if rect.contains(point):
                return aoi
        if (
            self.tablet is not None
            and self.tablet_mapping_verified
            and self.tablet.contains(point)
        ):
            return Aoi.TABLET
        if self.forward.contains(point):
            return Aoi.FORWARD
        if any(region.contains(point) for region in self.other):
            return Aoi.OTHER
        return Aoi.NO_AOI


class ContinuousAoiTimer:
    __slots__ = (
        "_aoi",
        "_emitted",
        "_latest_at",
        "_started_at",
        "gap_policy_seconds",
        "threshold_seconds",
    )

    def __init__(
        self, gap_policy_seconds: float, threshold_seconds: float = 0.150
    ) -> None:
        self.gap_policy_seconds = gap_policy_seconds
        self.threshold_seconds = threshold_seconds
        self._aoi: Aoi | None = None
        self._started_at: float | None = None
        self._latest_at: float | None = None
        self._emitted = False

    def observe(self, aoi: Aoi, acquisition_seconds: float | None, valid: bool) -> bool:
        if not valid or acquisition_seconds is None:
            self._aoi = None
            self._started_at = None
            self._latest_at = None
            self._emitted = False
            return False
        continued = (
            self._aoi is aoi
            and self._latest_at is not None
            and acquisition_seconds - self._latest_at <= self.gap_policy_seconds
        )
        if not continued:
            self._aoi = aoi
            self._started_at = acquisition_seconds
            self._emitted = False
        self._latest_at = acquisition_seconds
        reached = (
            self._started_at is not None
            and acquisition_seconds - self._started_at >= self.threshold_seconds
        )
        if reached and not self._emitted:
            self._emitted = True
            return True
        return False
