from __future__ import annotations

from dataclasses import dataclass

import pygame
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.scenario.driver_hud import HudState
from src.scenario.driver_view import CameraFeed
from src.scenario.research_noa_view import ResearchDriverView, ResearchDriverViewConfig
from src.vehicle.driving_mode import DrivingMode
from tests.test_driver_view import FakeHero, FakeSensor, FakeWorld


@dataclass(slots=True)
class RecordingHudRenderer:
    states: list[HudState]

    def draw(self, screen: pygame.Surface, state: HudState) -> None:
        self.states.append(state)


@pytest.mark.parametrize("cockpit_view", [False, True])
def test_research_hud_reads_canonical_state_each_frame(cockpit_view: bool) -> None:
    canonical_state = [
        AutomationState(
            AutomationAvailability.AVAILABLE,
            DrivingControlMode.MANUAL,
        )
    ]
    hero = FakeHero()
    viewer = ResearchDriverView(
        FakeWorld(),
        hero,
        ResearchDriverViewConfig(
            front_camera_only=True,
            automation_state_source=lambda: canonical_state[0],
        ),
    )
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer._cockpit_view = cockpit_view
    renderer = RecordingHudRenderer([])
    viewer._hud_renderer = renderer
    screen = pygame.Surface((1280, 720))

    viewer._draw(screen)
    canonical_state[0] = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )
    viewer._draw(screen)

    assert [state.mode_label for state in renderer.states] == [
        "MANUAL",
        "NOA_ACTIVE",
    ]
    assert [state.mode_toast_alpha for state in renderer.states] == [255, 255]
    assert viewer.driving_mode is DrivingMode.MANUAL
    assert hero.autopilot_calls == [False]
