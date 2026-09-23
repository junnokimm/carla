from __future__ import annotations

import pygame
import pytest

from src.scenario.driver_hud import (
    DriverHudRenderer,
    Gear,
    HudState,
    calculate_speed_kmh,
    driving_mode_label,
    gear_from_control,
)
from src.vehicle.driving_mode import DrivingMode


@pytest.mark.parametrize(
    ("velocity", "expected"),
    [
        ((0.0, 0.0, 0.0), 0.0),
        ((3.0, 4.0, 0.0), 18.0),
    ],
)
def test_calculate_speed_kmh_uses_velocity_magnitude(
    velocity: tuple[float, float, float], expected: float
) -> None:
    assert calculate_speed_kmh(*velocity) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("reverse", "gear", "expected"),
    [
        (True, 1, "R"),
        (False, -1, "R"),
        (False, 0, "N"),
        (False, 1, "D"),
        (False, 4, "D"),
    ],
)
def test_gear_display_normalizes_carla_control(
    reverse: bool, gear: int, expected: str
) -> None:
    assert gear_from_control(reverse=reverse, gear=gear) is Gear(expected)


def test_autonomous_mode_is_presented_as_autopilot() -> None:
    assert driving_mode_label(DrivingMode.AUTONOMOUS) == "AUTOPILOT"
    assert driving_mode_label(DrivingMode.MANUAL) == "MANUAL"


def test_persistent_hud_renders_speed_and_gear_without_driving_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_text: list[str] = []

    class RecordingFont:
        def render(
            self,
            text: str,
            antialias: bool,
            color: tuple[int, int, int],
        ) -> pygame.Surface:
            rendered_text.append(text)
            return pygame.Surface((max(1, len(text) * 10), 20), pygame.SRCALPHA)

    monkeypatch.setattr(
        pygame.font,
        "SysFont",
        lambda name, size, bold=False: RecordingFont(),
    )
    renderer = DriverHudRenderer(
        (1280, 720), anchor_ratio=(0.62, 0.66), toast_y_ratio=0.58
    )

    renderer.draw(
        pygame.Surface((1280, 720)),
        HudState(
            speed_kmh=82.0,
            gear=Gear.DRIVE,
            driving_mode=DrivingMode.AUTONOMOUS,
            mode_toast_alpha=None,
        ),
    )

    assert {"82", "km/h", "D"} <= set(rendered_text)
    assert "AUTOPILOT" not in rendered_text
    assert "MANUAL" not in rendered_text


@pytest.mark.parametrize(
    ("mode", "expected_label"),
    [
        (DrivingMode.AUTONOMOUS, "AUTOPILOT"),
        (DrivingMode.MANUAL, "MANUAL"),
    ],
)
def test_mode_toast_renders_only_when_active(
    monkeypatch: pytest.MonkeyPatch,
    mode: DrivingMode,
    expected_label: str,
) -> None:
    rendered_text: list[str] = []

    class RecordingFont:
        def render(
            self,
            text: str,
            antialias: bool,
            color: tuple[int, int, int],
        ) -> pygame.Surface:
            rendered_text.append(text)
            return pygame.Surface((max(1, len(text) * 10), 20), pygame.SRCALPHA)

    monkeypatch.setattr(
        pygame.font,
        "SysFont",
        lambda name, size, bold=False: RecordingFont(),
    )
    renderer = DriverHudRenderer(
        (1280, 720), anchor_ratio=(0.62, 0.66), toast_y_ratio=0.58
    )

    renderer.draw(
        pygame.Surface((1280, 720)),
        HudState(
            speed_kmh=82.0,
            gear=Gear.DRIVE,
            driving_mode=mode,
            mode_toast_alpha=220,
        ),
    )

    assert expected_label in rendered_text
