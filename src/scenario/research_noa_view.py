from __future__ import annotations

from typing import Protocol

import pygame

from src.scenario.driver_view import DriverView


class _KeyEvent(Protocol):
    type: int
    key: int


class ResearchDriverView(DriverView):
    def _handle_mode_events(self, events: list[_KeyEvent]) -> None:
        filtered_events = [
            event
            for event in events
            if not (event.type == pygame.KEYDOWN and event.key == pygame.K_p)
        ]
        super()._handle_mode_events(filtered_events)

    def _apply_manual_control(self) -> None:
        return


class ResearchLiveDriverView(ResearchDriverView):
    def _apply_manual_control(self) -> None:
        DriverView._apply_manual_control(self)
