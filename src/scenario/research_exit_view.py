from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import pygame

from src.experiment.exit_assistance import ExitPresentation
from src.scenario.research_exit_hud import ExitNavigationRenderer


class KeyEvent(Protocol):
    type: int
    key: int
    dict: Mapping[str, int]


class ExitViewKeyConfig(Protocol):
    @property
    def confirm_key(self) -> str: ...

    @property
    def reject_key(self) -> str: ...


class ExitViewBinding(Protocol):
    @property
    def config(self) -> ExitViewKeyConfig: ...

    def prepare_presentation(self) -> ExitPresentation | None: ...

    def mark_presented(self, rendered: ExitPresentation) -> None: ...


class ResearchExitViewController:
    def __init__(self, window_size: tuple[int, int]) -> None:
        self._binding: ExitViewBinding | None = None
        self._prepared: ExitPresentation | None = None
        self._presented_event_ids: set[str] = set()
        self._active_recommendation: ExitPresentation | None = None
        self._drawn = False
        self._keys_down: set[int] = set()
        self._renderer = ExitNavigationRenderer(window_size)

    def bind(self, binding: ExitViewBinding | None) -> None:
        self._binding = binding
        self._prepared = None
        self._presented_event_ids.clear()
        self._active_recommendation = None
        self._drawn = False
        self._keys_down.clear()

    def prepare(self) -> None:
        self._drawn = False
        self._prepared = (
            None if self._binding is None else self._binding.prepare_presentation()
        )
        if self._prepared is None or not self._prepared.recommendation:
            self._active_recommendation = None

    def draw(self, screen: pygame.Surface, *, hud_enabled: bool) -> None:
        if not hud_enabled or self._prepared is None or self._binding is None:
            self._active_recommendation = None
            return
        self._renderer.draw(
            screen,
            self._prepared,
            self._binding.config.confirm_key,
            self._binding.config.reject_key,
        )
        self._drawn = True

    def mark_after_flip(self) -> None:
        prepared = self._prepared
        if not self._drawn or prepared is None or self._binding is None:
            return
        if prepared.lc_event_id not in self._presented_event_ids:
            self._binding.mark_presented(prepared)
            self._presented_event_ids.add(prepared.lc_event_id)
        self._active_recommendation = prepared if prepared.recommendation else None

    def requests(self, events: list[KeyEvent]) -> tuple[bool, bool]:
        if self._binding is None:
            return False, False
        confirm_key = ord(self._binding.config.confirm_key)
        reject_key = ord(self._binding.config.reject_key)
        exit_keys = (confirm_key, reject_key)
        confirm_requested = False
        reject_requested = False
        for event in events:
            if event.type not in (pygame.KEYDOWN, pygame.KEYUP):
                continue
            if event.key not in exit_keys:
                continue
            if event.type == pygame.KEYUP:
                self._keys_down.discard(event.key)
                continue
            if event.type != pygame.KEYDOWN:
                continue
            if event.dict.get("repeat", 0) or event.key in self._keys_down:
                continue
            self._keys_down.add(event.key)
            if self._active_recommendation is None:
                continue
            if event.key == confirm_key:
                confirm_requested = True
            if event.key == reject_key:
                reject_requested = True
        return confirm_requested, reject_requested
