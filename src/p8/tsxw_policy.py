from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Final

import pygame

from src.experiment.automation_interaction import DriverInput

SDL_AXIS_POSITIVE_ENDPOINT: Final = 32767 / 32768


@dataclass(frozen=True, slots=True)
class TsxwConfig:
    index: int | None = None
    name: str | None = None
    steering_deadzone: float | None = None
    steering_left_axis: float = -1.0
    steering_center_axis: float = 0.0
    steering_right_axis: float = 1.0
    brake_pressed_axis: float = -1.0

    def __post_init__(self) -> None:
        if self.index is None and self.name is None:
            raise TsxwConfigError("an explicit index and/or name selector is required")
        if self.index is not None and self.index < 0:
            raise TsxwConfigError("index must be >= 0")
        if self.name is not None and not self.name.strip():
            raise TsxwConfigError("name must not be blank")
        if self.steering_deadzone is None:
            raise TsxwConfigError("steering deadzone must be explicit")
        values = (
            self.steering_left_axis,
            self.steering_center_axis,
            self.steering_right_axis,
            self.steering_deadzone,
            self.brake_pressed_axis,
        )
        if not all(isfinite(value) for value in values):
            raise TsxwConfigError("calibration values must be finite")
        if not (
            -1.0
            <= self.steering_left_axis
            < self.steering_center_axis
            < self.steering_right_axis
            <= 1.0
        ):
            raise TsxwConfigError("steering endpoints must be ordered within [-1, 1]")
        if self.steering_deadzone < 0.0 or self.steering_deadzone >= min(
            self.steering_center_axis - self.steering_left_axis,
            self.steering_right_axis - self.steering_center_axis,
        ):
            raise TsxwConfigError("steering deadzone exceeds the calibrated range")
        if not -1.0 <= self.brake_pressed_axis < 1.0:
            raise TsxwConfigError("brake pressed endpoint must be within [-1, 1)")


class TsxwConfigError(ValueError):
    def __init__(self, requirement: str) -> None:
        self.requirement = requirement
        super().__init__(requirement)

    def __str__(self) -> str:
        return self.requirement


@dataclass(frozen=True, slots=True)
class TsxwInputSample:
    raw_steering: float
    raw_brake: float
    raw_throttle: float
    steering: float
    brake: float
    throttle: float
    steering_engaged: bool
    left_pressed: bool
    right_pressed: bool
    noa_pressed: bool
    reject_pressed: bool
    left_held: bool
    right_held: bool
    noa_held: bool
    reject_held: bool


class TsxwCommandState:
    def __init__(self) -> None:
        self.keyboard_down: set[int] = set()
        self.wheel_down: set[int] = set()


def normalize_pedal(raw: float, *, pressed_axis: float) -> float:
    return min(1.0, max(0.0, (1.0 - raw) / (1.0 - pressed_axis)))


def normalize_brake_pedal(raw: float, *, pressed_axis: float) -> float:
    if raw == SDL_AXIS_POSITIVE_ENDPOINT:
        return 0.0
    return normalize_pedal(raw, pressed_axis=pressed_axis)


def normalize_steering(raw: float, config: TsxwConfig) -> float:
    center = config.steering_center_axis
    deadzone = config.steering_deadzone
    if deadzone is None:
        raise TsxwConfigError("steering deadzone must be explicit")
    if raw < center - deadzone:
        normalized = (raw - (center - deadzone)) / (
            center - deadzone - config.steering_left_axis
        )
    elif raw > center + deadzone:
        normalized = (raw - (center + deadzone)) / (
            config.steering_right_axis - center - deadzone
        )
    else:
        return 0.0
    return min(1.0, max(-1.0, normalized))


def merge_driver_input(
    keyboard: DriverInput,
    wheel: TsxwInputSample,
    *,
    throttle_override: bool,
    brake_override: bool,
    steering_override: bool,
) -> DriverInput:
    return DriverInput(
        throttle=keyboard.throttle if throttle_override else wheel.throttle,
        brake=keyboard.brake if brake_override else wheel.brake,
        steering=keyboard.steering if steering_override else wheel.steering,
        steering_engaged=(
            keyboard.steering_engaged if steering_override else wheel.steering_engaged
        ),
        hand_brake=keyboard.hand_brake,
        activation_requested=keyboard.activation_requested,
        deactivation_requested=keyboard.deactivation_requested,
        exit_confirm_requested=keyboard.exit_confirm_requested,
        exit_reject_requested=keyboard.exit_reject_requested,
        lane_change_direction=keyboard.lane_change_direction,
        lane_change_in_progress=keyboard.lane_change_in_progress,
        stage=keyboard.stage,
    )


def coalesce_command_events(
    state: TsxwCommandState,
    events: Sequence[pygame.event.Event],
    wheel: TsxwInputSample,
    *,
    noa_key: int,
) -> list[pygame.event.Event]:
    fixed_command_keys = (pygame.K_z, pygame.K_x, pygame.K_r)
    if noa_key in fixed_command_keys:
        raise TsxwConfigError("noa_key must differ from R, Z, and X")

    command_keys = (pygame.K_z, pygame.K_x, noa_key, pygame.K_r)
    command_key_set = set(command_keys)
    keyboard_before = set(state.keyboard_down)
    wheel_before = set(state.wheel_down)
    keyboard_after = set(keyboard_before)
    for event in events:
        if event.type == pygame.WINDOWFOCUSLOST:
            keyboard_after.clear()
        elif event.type == pygame.KEYDOWN and event.key in command_key_set:
            if not event.dict.get("repeat", 0):
                keyboard_after.add(event.key)
        elif event.type == pygame.KEYUP and event.key in command_key_set:
            keyboard_after.discard(event.key)

    wheel_after = {
        key
        for key, held in (
            (pygame.K_z, wheel.left_held),
            (pygame.K_x, wheel.right_held),
            (noa_key, wheel.noa_held),
            (pygame.K_r, wheel.reject_held),
        )
        if held
    }
    handoff_keys = {
        key
        for key in command_keys
        if (key in keyboard_before or key in wheel_before)
        == (key in keyboard_after or key in wheel_after)
        and (key in keyboard_before) != (key in keyboard_after)
        and (key in wheel_before) != (key in wheel_after)
    }
    logical_down = {
        key: key in keyboard_before or key in wheel_before for key in command_keys
    }
    output_down = dict(logical_down)
    keydown_emitted: set[int] = set()
    combined: list[pygame.event.Event] = []

    def emit_transition(key: int, down: bool) -> None:
        logical_down[key] = down
        if key in handoff_keys:
            return
        if down:
            if key not in keydown_emitted:
                combined.append(pygame.event.Event(pygame.KEYDOWN, key=key, repeat=0))
                keydown_emitted.add(key)
                output_down[key] = True
        elif output_down[key]:
            combined.append(pygame.event.Event(pygame.KEYUP, key=key, repeat=0))
            output_down[key] = False

    for event in events:
        if event.type == pygame.WINDOWFOCUSLOST:
            combined.append(event)
            state.keyboard_down.clear()
            for key in command_keys:
                down = key in wheel_after
                if down != logical_down[key]:
                    emit_transition(key, down)
            continue
        if event.type not in (pygame.KEYDOWN, pygame.KEYUP):
            combined.append(event)
            continue
        key = event.key
        if key not in command_key_set:
            combined.append(event)
            continue
        if event.type == pygame.KEYDOWN:
            if event.dict.get("repeat", 0):
                continue
            state.keyboard_down.add(key)
        else:
            state.keyboard_down.discard(key)
        down = key in state.keyboard_down or key in wheel_after
        if down != logical_down[key]:
            emit_transition(key, down)

    for key in command_keys:
        down = key in state.keyboard_down or key in wheel_after
        if down != logical_down[key]:
            emit_transition(key, down)
    state.wheel_down = wheel_after
    return combined
