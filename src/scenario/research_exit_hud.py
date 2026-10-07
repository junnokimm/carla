from __future__ import annotations

from typing import Final

import pygame

from src.experiment.exit_assistance import ExitPresentation
from src.scenario.driver_hud import HUD_FOREGROUND, HUD_SECONDARY, HUD_SHADOW

NAVIGATION_TOP_UNITS: Final = 7
CONTROL_GAP_UNITS: Final = 2
ARROW_GAP_UNITS: Final = 3
SHADOW_OFFSET_UNITS: Final = 0.5
BASE_UNIT: Final = 4


class ExitNavigationRenderer:
    """Render exit guidance in the windshield's non-mirror top-center area."""

    def __init__(self, window_size: tuple[int, int]) -> None:
        pygame.font.init()
        width, height = window_size
        self._center_x = width // 2
        self._navigation_top = NAVIGATION_TOP_UNITS * BASE_UNIT
        self._control_gap = CONTROL_GAP_UNITS * BASE_UNIT
        self._arrow_gap = ARROW_GAP_UNITS * BASE_UNIT
        self._shadow_offset = round(SHADOW_OFFSET_UNITS * BASE_UNIT)
        self._navigation_font = pygame.font.SysFont(
            "malgungothic", max(20, round(height * 0.032)), bold=True
        )
        self._control_font = pygame.font.SysFont(
            "malgungothic", max(14, round(height * 0.022)), bold=True
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
        controls = self._control_font.render(
            f"{confirm_key.upper()} 확인    {reject_key.upper()} 거부",
            True,
            HUD_SECONDARY,
        )
        controls_shadow = self._control_font.render(
            f"{confirm_key.upper()} 확인    {reject_key.upper()} 거부",
            True,
            HUD_SHADOW,
        )
        controls_rect = controls.get_rect(
            midtop=(
                self._center_x,
                navigation_rect.bottom + self._control_gap,
            )
        )
        screen.blit(
            controls_shadow,
            controls_rect.move(self._shadow_offset, self._shadow_offset),
        )
        screen.blit(controls, controls_rect)

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
