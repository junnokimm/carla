from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pygame

from src.scenario.driver_hud import HUD_FOREGROUND, HUD_SECONDARY, HUD_SHADOW


@dataclass(frozen=True, slots=True)
class Preparation:
    index: int
    target: str
    monitor_label: str
    ready: bool
    confirmed: bool


class ValidationKeys:
    def __init__(self) -> None:
        self._held: set[int] = set()

    def edges(self, events: Sequence[pygame.event.Event]) -> set[int]:
        pressed = set()
        for event in events:
            if event.type == pygame.KEYUP:
                self._held.discard(event.key)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or (event.key not in self._held and not event.dict.get("repeat", False)):
                    pressed.add(event.key)
                self._held.add(event.key)
        return pressed


class PreparationOverlay:
    def __init__(self) -> None:
        pygame.font.init()
        self.title = pygame.font.SysFont("malgungothic", 28, bold=True)
        self.body = pygame.font.SysFont("malgungothic", 22)
        self.detail = pygame.font.SysFont("malgungothic", 18)

    def draw(self, screen: pygame.Surface, preparation: Preparation | None) -> None:
        if preparation is None:
            return
        instruction = ("눈을 감거나 추적 범위 밖을 볼 준비를 하고 Space" if preparation.target == "MISSING"
                       else "대상을 바라볼 준비를 하고 Space")
        if not preparation.confirmed:
            instruction = "ETM에 설정한 표시 영역과 이 모니터가 같으면 C"
        if not preparation.ready:
            instruction = "카메라와 실제 gaze 데이터 준비 중 (ESC: 중단)"
        lines = (
            (self.title, f"PREPARE {preparation.index + 1}/5  {preparation.target}", HUD_FOREGROUND),
            (self.body, instruction, HUD_FOREGROUND),
            (self.detail, "측정 2초 동안 안내가 숨겨집니다. 언제든 ESC로 중단", HUD_SECONDARY),
            (self.detail, preparation.monitor_label, HUD_SECONDARY),
        )
        for index, (font, text, color) in enumerate(lines):
            image = font.render(text, True, color)
            shadow = font.render(text, True, HUD_SHADOW)
            position = image.get_rect(center=(screen.get_width() // 2, screen.get_height() // 2 - 60 + 40 * index))
            screen.blit(shadow, position.move(2, 2))
            screen.blit(image, position)
