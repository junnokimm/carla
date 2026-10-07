from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenRect:
    """Immutable screen-space rectangle shared by rendering and AOI consumers."""

    x: int
    y: int
    width: int
    height: int

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height

    @property
    def position(self) -> tuple[int, int]:
        return self.x, self.y

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def intersects(self, other: ScreenRect) -> bool:
        return (
            self.x < other.x + other.width
            and self.x + self.width > other.x
            and self.y < other.y + other.height
            and self.y + self.height > other.y
        )


@dataclass(frozen=True, slots=True)
class MirrorLayoutSpec:
    window_size: tuple[int, int]
    side_size: tuple[int, int]
    rear_size: tuple[int, int]
    edge_margin: int
    rear_top_ratio: float


@dataclass(frozen=True, slots=True)
class MirrorLayout:
    """Named mirror rectangles forming the composition and future AOI source."""

    front_position: tuple[int, int]
    rear_mirror: ScreenRect
    left_mirror: ScreenRect
    right_mirror: ScreenRect

    @property
    def left_mirror_position(self) -> tuple[int, int]:
        return self.left_mirror.position

    @property
    def right_mirror_position(self) -> tuple[int, int]:
        return self.right_mirror.position

    def rect_for(self, role: str) -> ScreenRect | None:
        return next(
            (
                rect
                for mirror_role, rect in (
                    ("rear", self.rear_mirror),
                    ("left", self.left_mirror),
                    ("right", self.right_mirror),
                )
                if mirror_role == role
            ),
            None,
        )


def calculate_mirror_layout(spec: MirrorLayoutSpec) -> MirrorLayout:
    window_width, window_height = spec.window_size
    side_width, side_height = spec.side_size
    rear_width, rear_height = spec.rear_size
    side_y = window_height - spec.edge_margin - side_height
    rear_y = round(window_height * spec.rear_top_ratio)
    return MirrorLayout(
        front_position=(0, 0),
        rear_mirror=ScreenRect(
            x=(window_width - rear_width) // 2 + 500,
            y=rear_y,
            width=rear_width,
            height=rear_height,
        ),
        left_mirror=ScreenRect(
            x=spec.edge_margin,
            y=side_y,
            width=side_width,
            height=side_height,
        ),
        right_mirror=ScreenRect(
            x=window_width - side_width - spec.edge_margin,
            y=side_y,
            width=side_width,
            height=side_height,
        ),
    )
