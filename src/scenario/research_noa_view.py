from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import carla
import pygame

from src.scenario.driver_view import (
    CAMERA_BLUEPRINT_ID,
    CameraFeed,
    DriverView,
    DriverViewConfig,
    RuntimeIterationScheduler,
)
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics


class _KeyEvent(Protocol):
    type: int
    key: int


@dataclass(frozen=True, slots=True)
class ResearchDriverViewConfig(DriverViewConfig):
    front_camera_only: bool = False


class ResearchDriverView(DriverView):
    _diagnostics: ResearchNoADiagnostics | None = None

    def __init__(
        self,
        world: carla.World,
        hero: carla.Actor,
        config: ResearchDriverViewConfig | None = None,
    ) -> None:
        self._research_config = config or ResearchDriverViewConfig()
        super().__init__(world, hero, self._research_config)

    def attach(self) -> None:
        if not self._research_config.front_camera_only:
            super().attach()
            return
        blueprint = self._world.get_blueprint_library().find(CAMERA_BLUEPRINT_ID)
        width, height = self._research_config.front_resolution
        blueprint.set_attribute("image_size_x", str(width))
        blueprint.set_attribute("image_size_y", str(height))
        blueprint.set_attribute("fov", str(self._research_config.fov))
        blueprint.set_attribute("exposure_compensation", "0.5")
        sensor = self._world.spawn_actor(
            blueprint,
            self._research_config.front_transform.to_carla(),
            attach_to=self._hero,
        )
        feed = CameraFeed(role="front", sensor=sensor)
        self._feeds.append(feed)
        sensor.listen(feed.receive)

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
        if self._research_config.front_camera_only:
            front = self._feeds[0]
            self._feeds.extend(
                (
                    CameraFeed(role="left", sensor=front.sensor),
                    CameraFeed(role="right", sensor=front.sensor),
                )
            )
        try:
            if diagnostics is None:
                super()._draw(screen)
                return
            started_at = diagnostics.timestamp()
            super()._draw(screen)
            diagnostics.record_render_duration(diagnostics.timestamp() - started_at)
        finally:
            if self._research_config.front_camera_only:
                del self._feeds[1:]

    def _apply_manual_control(self) -> None:
        return


class ResearchLiveDriverView(ResearchDriverView):
    def _apply_manual_control(self) -> None:
        DriverView._apply_manual_control(self)
