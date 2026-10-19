from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pygame

from src.scenario.driver_hud import HUD_FOREGROUND, HUD_SECONDARY, HUD_SHADOW

BACKGROUND: Final = HUD_SHADOW
PANEL: Final = (12, 30, 34)
SPACE: Final = 4
MARGIN: Final = SPACE * 8
PANEL_INSET: Final = SPACE * 6
LINE_GAP: Final = SPACE * 3


@dataclass(frozen=True, slots=True)
class OperatorPage:
    eyebrow: str
    title: str
    body: tuple[str, ...]
    controls: tuple[str, ...]
    status: str = ""


class OperatorRenderer:
    def __init__(self, window_size: tuple[int, int]) -> None:
        pygame.font.init()
        self._window_size = window_size
        self._title = pygame.font.SysFont("malgungothic", 32, bold=True)
        self._body = pygame.font.SysFont("malgungothic", 18)
        self._label = pygame.font.SysFont("malgungothic", 16, bold=True)
        self._mono = pygame.font.SysFont("malgungothic", 16, bold=True)

    def draw(self, surface: pygame.Surface, page: OperatorPage) -> None:
        surface.fill(BACKGROUND)
        width, height = self._window_size
        panel = pygame.Rect(MARGIN, MARGIN, width - MARGIN * 2, height - MARGIN * 2)
        pygame.draw.rect(surface, PANEL, panel)
        pygame.draw.rect(surface, HUD_SECONDARY, panel, width=1)
        content_width = panel.width - PANEL_INSET * 2
        y = panel.top + PANEL_INSET
        y = self._line(
            surface,
            self._label,
            page.eyebrow,
            (panel.left + PANEL_INSET, y),
            HUD_SECONDARY,
        )
        y += SPACE * 2
        y = self._line(
            surface,
            self._title,
            page.title,
            (panel.left + PANEL_INSET, y),
            HUD_FOREGROUND,
        )
        y += SPACE * 5
        for paragraph in page.body:
            y = self._wrapped(
                surface, paragraph, panel.left + PANEL_INSET, y, content_width
            )
            y += LINE_GAP
        controls_top = max(y + SPACE * 2, panel.bottom - PANEL_INSET - 118)
        pygame.draw.line(
            surface,
            HUD_SECONDARY,
            (panel.left + PANEL_INSET, controls_top),
            (panel.right - PANEL_INSET, controls_top),
        )
        y = controls_top + SPACE * 4
        for control in page.controls:
            y = self._line(
                surface,
                self._mono,
                control,
                (panel.left + PANEL_INSET, y),
                HUD_SECONDARY,
            )
            y += SPACE
        if page.status:
            status = self._label.render(page.status, True, HUD_FOREGROUND)
            surface.blit(
                status,
                status.get_rect(
                    bottomright=(panel.right - PANEL_INSET, panel.bottom - PANEL_INSET)
                ),
            )

    @staticmethod
    def _line(
        surface: pygame.Surface,
        font: pygame.font.Font,
        text: str,
        position: tuple[int, int],
        color: tuple[int, int, int],
    ) -> int:
        rendered = font.render(text, True, color)
        surface.blit(rendered, position)
        return position[1] + rendered.get_height()

    def _wrapped(
        self,
        surface: pygame.Surface,
        text: str,
        left: int,
        top: int,
        width: int,
    ) -> int:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            if self._body.size(word)[0] > width:
                if current:
                    lines.append(current)
                    current = ""
                for character in word:
                    if current and self._body.size(current + character)[0] > width:
                        lines.append(current)
                        current = ""
                    current += character
                continue
            candidate = word if not current else f"{current} {word}"
            if current and self._body.size(candidate)[0] > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        y = top
        for line in lines:
            y = (
                self._line(surface, self._body, line, (left, y), HUD_FOREGROUND)
                + SPACE * 2
            )
        return y
