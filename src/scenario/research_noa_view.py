from __future__ import annotations

from typing import Protocol

import pygame

from src.scenario.driver_view import DriverView, RuntimeIterationScheduler
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics


class _KeyEvent(Protocol):
    type: int
    key: int


class ResearchDriverView(DriverView):
    _diagnostics: ResearchNoADiagnostics | None = None

    def run(
        self,
        duration: float,
        scheduler: RuntimeIterationScheduler | None = None,
        diagnostics: ResearchNoADiagnostics | None = None,
    ) -> bool:
        self._diagnostics = diagnostics
        try:
            return super().run(duration, scheduler)
        finally:
            if self._diagnostics is not None:
                self._diagnostics.discard_incomplete_driver_loop()
            self._diagnostics = None

    def _handle_mode_events(self, events: list[_KeyEvent]) -> None:
        if self._diagnostics is not None:
            self._diagnostics.begin_driver_loop()
        filtered_events = [
            event
            for event in events
            if not (event.type == pygame.KEYDOWN and event.key == pygame.K_p)
        ]
        super()._handle_mode_events(filtered_events)

    def _draw(self, screen: pygame.Surface) -> None:
        diagnostics = self._diagnostics
        if diagnostics is None:
            super()._draw(screen)
            return
        started_at = diagnostics.timestamp()
        super()._draw(screen)
        diagnostics.record_render_duration(diagnostics.timestamp() - started_at)

    def _apply_manual_control(self) -> None:
        return


class ResearchLiveDriverView(ResearchDriverView):
    def _apply_manual_control(self) -> None:
        DriverView._apply_manual_control(self)
