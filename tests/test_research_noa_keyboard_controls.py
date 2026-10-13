from __future__ import annotations

from collections import defaultdict

import pygame
import pytest

from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from src.vehicle.driving_mode import DrivingMode
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeWorld


def test_research_keyboard_w_release_s_applies_expected_manual_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_frames: list[defaultdict[int, bool]] = []
    for pressed_key in (pygame.K_w, None, pygame.K_s):
        keys: defaultdict[int, bool] = defaultdict(bool)
        if pressed_key is not None:
            keys[pressed_key] = True
        key_frames.append(keys)
    frames = iter(key_frames)
    monkeypatch.setattr(pygame.key, "get_pressed", frames.__next__)
    hero = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        hero,
        ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            manual_control_limits=ResearchManualControlLimits(1.0, 0.5, 0.15),
        ),
    )

    for _ in key_frames:
        viewer._pending_driver_input = viewer._collect_driver_input(())
        viewer._apply_manual_control()

    accelerate, released, braking = hero.applied_controls
    assert (accelerate.throttle, accelerate.brake) == pytest.approx((1.0, 0.0))
    assert (released.throttle, released.brake) == pytest.approx((0.0, 0.0))
    assert (braking.throttle, braking.brake) == pytest.approx((0.0, 0.5))
    assert all(not control.reverse for control in hero.applied_controls)
    assert all(not control.manual_gear_shift for control in hero.applied_controls)
