from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import pygame
from pygame._sdl2.video import Window

from src.scenario.camera_feed import DriverViewPerformanceObserver
from src.scenario.windows_display import current_monitor_bounds

LETTERBOX_COLOR: Final = (0, 0, 0)


def aspect_fit_rect(
    logical_size: tuple[int, int], display_size: tuple[int, int]
) -> pygame.Rect:
    logical_width, logical_height = logical_size
    display_width, display_height = display_size
    if display_width * logical_height <= display_height * logical_width:
        width = display_width
        height = display_width * logical_height // logical_width
    else:
        height = display_height
        width = display_height * logical_width // logical_height
    return pygame.Rect(
        (display_width - width) // 2,
        (display_height - height) // 2,
        width,
        height,
    )


class DriverViewPresentation:
    def __init__(
        self,
        logical_size: tuple[int, int],
        display_surface: pygame.Surface | None,
    ) -> None:
        self.canvas = pygame.Surface(logical_size)
        self._logical_size = logical_size
        self._display_surface = display_surface
        self._scaled_canvas: pygame.Surface | None = None
        self._fullscreen = False
        self._f11_down = False
        self._windowed_size: tuple[int, int] | None = None
        self._windowed_position: tuple[int, int] | None = None
        self._display_index: int | None = None
        self._window: Window | None = None
        self.performance_observer: DriverViewPerformanceObserver | None = None
        self.gaze_observer = None
        self.gaze_transition_observer = None

    def handle_events(self, events: Sequence[pygame.event.Event]) -> None:
        for event in events:
            if event.type == pygame.WINDOWFOCUSLOST or (
                event.type == pygame.KEYUP and event.key == pygame.K_F11
            ):
                self._f11_down = False
            elif (
                event.type == pygame.KEYDOWN
                and event.key == pygame.K_F11
                and not event.dict.get("repeat", 0)
                and not self._f11_down
            ):
                self._f11_down = True
                observer = self.performance_observer
                started = observer.timestamp() if observer is not None else 0.0
                try:
                    self._toggle_fullscreen()
                finally:
                    if observer is not None:
                        observer.record_duration(
                            "fullscreen_transition", observer.timestamp() - started
                        )

    def present(self) -> None:
        target = self._display_surface
        if target is None:
            return
        if self.gaze_observer is not None:
            try:
                self.gaze_observer(target.get_size())
            except Exception:  # noqa: BLE001 -- optional observer cannot stop driving
                # Optional observation must never interrupt driving or rendering.
                self.gaze_observer = None
        target.fill(LETTERBOX_COLOR)
        destination = aspect_fit_rect(self._logical_size, target.get_size())
        if destination.size == self._logical_size:
            target.blit(self.canvas, destination.topleft)
            return
        if (
            self._scaled_canvas is None
            or self._scaled_canvas.get_size() != destination.size
        ):
            self._scaled_canvas = pygame.Surface(destination.size)
        pygame.transform.smoothscale(
            self.canvas,
            destination.size,
            self._scaled_canvas,
        )
        target.blit(self._scaled_canvas, destination.topleft)

    def _toggle_fullscreen(self) -> None:
        if self.gaze_transition_observer is not None:
            try:
                self.gaze_transition_observer()
            except Exception:  # noqa: BLE001 -- optional gaze cannot block display switching
                self.gaze_transition_observer = None
        if self._window is None:
            # pygame 2.6.1 stores a non-owning pg_window pointer for SDL events.
            # Keep the borrowed wrapper alive until DriverView shuts down SDL.
            self._window = Window.from_display_module()
        if self._fullscreen:
            assert self._windowed_size is not None
            assert self._windowed_position is not None
            assert self._display_index is not None
            window = self._window
            window.set_windowed()
            self._display_surface = pygame.display.set_mode(
                self._windowed_size,
                display=self._display_index,
            )
            window.position = self._windowed_position
            self._fullscreen = False
            self._scaled_canvas = None
            return
        self._windowed_size = pygame.display.get_window_size()
        self._windowed_position = self._window.position
        self._display_index = self._window.display_index
        monitor_x, monitor_y, monitor_width, monitor_height = current_monitor_bounds()
        self._display_surface = pygame.display.set_mode(
            (monitor_width, monitor_height),
            pygame.NOFRAME,
            display=self._display_index,
        )
        self._window.position = (monitor_x, monitor_y)
        self._fullscreen = True
        self._scaled_canvas = None
