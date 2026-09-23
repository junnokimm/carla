from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, assert_never

import pygame

from src.vehicle.driving_mode import DrivingMode


class Gear(StrEnum):
    """Transmission directions shown by the windshield HUD."""

    DRIVE = "D"
    NEUTRAL = "N"
    REVERSE = "R"


@dataclass(frozen=True, slots=True)
class HudState:
    """Vehicle telemetry needed for one HUD frame."""

    speed_kmh: float
    gear: Gear
    driving_mode: DrivingMode
    mode_toast_alpha: int | None = None


class HudRenderer(Protocol):
    """Draw a typed HUD state without owning the vehicle that produced it."""

    def draw(self, screen: pygame.Surface, state: HudState) -> None: ...


def calculate_speed_kmh(x: float, y: float, z: float) -> float:
    """Convert a CARLA velocity vector expressed in metres per second to km/h."""
    return math.sqrt(x**2 + y**2 + z**2) * 3.6


def gear_from_control(*, reverse: bool, gear: int) -> Gear:
    """Normalize CARLA transmission details to a compact D/N/R display."""
    if reverse or gear < 0:
        return Gear.REVERSE
    if gear == 0:
        return Gear.NEUTRAL
    return Gear.DRIVE


def driving_mode_label(mode: DrivingMode) -> str:
    """Return the driver-facing label for the authoritative driving mode."""
    match mode:
        case DrivingMode.AUTONOMOUS:
            return "AUTOPILOT"
        case DrivingMode.MANUAL:
            return "MANUAL"
        case unreachable:
            assert_never(unreachable)


class DriverHudRenderer:
    """Render restrained persistent and temporary windshield information."""

    def __init__(
        self,
        window_size: tuple[int, int],
        *,
        anchor_ratio: tuple[float, float],
        toast_y_ratio: float,
    ) -> None:
        pygame.font.init()
        width, height = window_size
        self._anchor = (
            round(width * anchor_ratio[0]),
            round(height * anchor_ratio[1]),
        )
        self._toast_center = (self._anchor[0], round(height * toast_y_ratio))
        self._speed_font = pygame.font.SysFont(
            "segoeui", max(38, round(height * 0.075))
        )
        self._unit_font = pygame.font.SysFont(
            "segoeui", max(13, round(height * 0.020))
        )
        self._gear_font = pygame.font.SysFont(
            "segoeui", max(18, round(height * 0.030)), bold=True
        )
        self._toast_font = pygame.font.SysFont(
            "segoeui", max(18, round(height * 0.028)), bold=True
        )

    def draw(self, screen: pygame.Surface, state: HudState) -> None:
        """Draw persistent speed/gear and an optional driving-mode toast."""
        speed_text = f"{state.speed_kmh:.0f}"
        foreground = (224, 245, 241)
        secondary = (166, 214, 208)
        shadow = (5, 15, 18)

        speed = self._speed_font.render(speed_text, True, foreground)
        speed.set_alpha(232)
        speed_shadow = self._speed_font.render(speed_text, True, shadow)
        speed_shadow.set_alpha(155)
        unit = self._unit_font.render("km/h", True, secondary)
        unit.set_alpha(215)
        unit_shadow = self._unit_font.render("km/h", True, shadow)
        unit_shadow.set_alpha(145)

        group_width = speed.get_width() + 10 + unit.get_width()
        group_left = self._anchor[0] - group_width // 2
        speed_rect = speed.get_rect(topleft=(group_left, self._anchor[1]))
        unit_rect = unit.get_rect(
            bottomleft=(speed_rect.right + 10, speed_rect.bottom - 5)
        )
        screen.blit(speed_shadow, speed_rect.move(2, 2))
        screen.blit(speed, speed_rect)
        screen.blit(unit_shadow, unit_rect.move(1, 1))
        screen.blit(unit, unit_rect)

        gear = self._gear_font.render(state.gear.value, True, secondary)
        gear.set_alpha(220)
        gear_shadow = self._gear_font.render(state.gear.value, True, shadow)
        gear_shadow.set_alpha(145)
        gear_rect = gear.get_rect(midtop=(self._anchor[0], speed_rect.bottom + 7))
        screen.blit(gear_shadow, gear_rect.move(1, 1))
        screen.blit(gear, gear_rect)

        if state.mode_toast_alpha is None:
            return
        toast_text = driving_mode_label(state.driving_mode)
        toast = self._toast_font.render(toast_text, True, foreground)
        toast.set_alpha(state.mode_toast_alpha)
        toast_shadow = self._toast_font.render(toast_text, True, shadow)
        toast_shadow.set_alpha(round(state.mode_toast_alpha * 0.68))
        toast_rect = toast.get_rect(center=self._toast_center)
        screen.blit(toast_shadow, toast_rect.move(2, 2))
        screen.blit(toast, toast_rect)
