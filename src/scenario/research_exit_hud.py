from __future__ import annotations

from typing import Final

import pygame

from src.experiment.exit_assistance import ExitPresentation
from src.scenario.driver_hud import (
    DRIVER_NOTICE_TOP_RATIO,
    HUD_FOREGROUND,
    HUD_SECONDARY,
    HUD_SHADOW,
)

ARROW_GAP_UNITS: Final = 3
SHADOW_OFFSET_UNITS: Final = 0.5
BASE_UNIT: Final = 4


class ExitNavigationRenderer:
    """Render centered exit guidance below the speed/gear HUD."""

    def __init__(self, window_size: tuple[int, int]) -> None:
        pygame.font.init()
        width, height = window_size
        self._center_x = width // 2
        self._navigation_top = round(height * DRIVER_NOTICE_TOP_RATIO)
        self._arrow_gap = ARROW_GAP_UNITS * BASE_UNIT
        self._shadow_offset = round(SHADOW_OFFSET_UNITS * BASE_UNIT)
        self._navigation_font = pygame.font.SysFont(
            "malgungothic", max(20, round(height * 0.032)), bold=True
        )

    def draw(
        self,
        screen: pygame.Surface,
        presentation: ExitPresentation,
        confirm_key: str,
        reject_key: str,
    ) -> None:
        navigation = self._navigation_font.render(
            presentation.navigation_text, True, HUD_FOREGROUND
        )
        navigation_shadow = self._navigation_font.render(
            presentation.navigation_text, True, HUD_SHADOW
        )
        navigation_rect = navigation.get_rect(
            midtop=(self._center_x, self._navigation_top)
        )
        screen.blit(
            navigation_shadow,
            navigation_rect.move(self._shadow_offset, self._shadow_offset),
        )
        screen.blit(navigation, navigation_rect)
        if not presentation.recommendation:
            return
        self._draw_right_arrow(screen, navigation_rect)

    def _draw_right_arrow(
        self,
        screen: pygame.Surface,
        navigation_rect: pygame.Rect,
    ) -> None:
        center_y = navigation_rect.centery
        start_x = navigation_rect.right + self._arrow_gap
        length = BASE_UNIT * 5
        head = BASE_UNIT * 2
        width = max(2, BASE_UNIT // 2)
        end_x = start_x + length
        shadow_offset = self._shadow_offset
        shadow_points = (
            (start_x + shadow_offset, center_y + shadow_offset),
            (end_x + shadow_offset, center_y + shadow_offset),
            (end_x - head + shadow_offset, center_y - head + shadow_offset),
            (end_x + shadow_offset, center_y + shadow_offset),
            (end_x - head + shadow_offset, center_y + head + shadow_offset),
        )
        points = (
            (start_x, center_y),
            (end_x, center_y),
            (end_x - head, center_y - head),
            (end_x, center_y),
            (end_x - head, center_y + head),
        )
        pygame.draw.lines(screen, HUD_SHADOW, False, shadow_points, width)
        pygame.draw.lines(screen, HUD_SECONDARY, False, points, width)
