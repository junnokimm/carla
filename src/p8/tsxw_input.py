from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Final

import pygame

from src.error_notes import append_error_note
from src.p8.tsxw_policy import (
    TsxwConfig,
    TsxwInputSample,
    normalize_brake_pedal,
    normalize_pedal,
    normalize_steering,
)

TSXW_NAME: Final = "TS-XW Racer"
STEERING_AXIS: Final = 0
BRAKE_AXIS: Final = 1
THROTTLE_AXIS: Final = 2
LEFT_BUTTON: Final = 9
RIGHT_BUTTON: Final = 8
NOA_BUTTON: Final = 5
REJECT_BUTTON: Final = 7


class TsxwDeviceError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class TsxwLogEvent:
    event_type: str
    payload_json: str


class TsxwInput:
    def __init__(
        self,
        config: TsxwConfig,
        joystick: pygame.joystick.JoystickType,
        index: int,
        owns_subsystem: bool,
        event_sink: Callable[[TsxwLogEvent], None] | None,
    ) -> None:
        self.config = config
        self._joystick = joystick
        self._index = index
        self._instance_id = joystick.get_instance_id()
        self._owns_subsystem = owns_subsystem
        self._event_sink = event_sink
        self._previous_buttons = (False, False, False, False)
        self._closed = False

    @classmethod
    def open(
        cls,
        config: TsxwConfig,
        *,
        event_sink: Callable[[TsxwLogEvent], None] | None = None,
    ) -> TsxwInput:
        owns_subsystem = not pygame.joystick.get_init()
        if owns_subsystem:
            pygame.joystick.init()
        selected: pygame.joystick.JoystickType | None = None
        try:
            candidates: list[tuple[int, pygame.joystick.JoystickType]] = []
            for index in range(pygame.joystick.get_count()):
                joystick = pygame.joystick.Joystick(index)
                if config.index is not None and index != config.index:
                    continue
                if config.name is not None and joystick.get_name() != config.name:
                    continue
                candidates.append((index, joystick))
            if not candidates:
                raise TsxwDeviceError("no matching TS-XW device")
            if len(candidates) != 1:
                raise TsxwDeviceError("ambiguous TS-XW device selection")
            index, selected = candidates[0]
            selected.init()
            name = selected.get_name()
            if name != TSXW_NAME:
                raise TsxwDeviceError(
                    f"selected device name is {name!r}, expected {TSXW_NAME!r}"
                )
            shape = (
                selected.get_numaxes(),
                selected.get_numbuttons(),
                selected.get_numhats(),
            )
            if shape != (4, 13, 1):
                raise TsxwDeviceError(
                    f"TS-XW shape mismatch: axes={shape[0]} buttons={shape[1]} hats={shape[2]}"
                )
            adapter = cls(config, selected, index, owns_subsystem, event_sink)
            adapter._emit(
                "tsxw_device_opened",
                {
                    "index": index,
                    "instance_id": adapter._instance_id,
                    "name": name,
                    "guid": selected.get_guid(),
                    "axes": shape[0],
                    "buttons": shape[1],
                    "hats": shape[2],
                    "steering_deadzone": config.steering_deadzone,
                    "steering_left_axis": config.steering_left_axis,
                    "steering_center_axis": config.steering_center_axis,
                    "steering_right_axis": config.steering_right_axis,
                    "brake_pressed_axis": config.brake_pressed_axis,
                    "brake_calibration_status": (
                        "NOMINAL_PROVISIONAL"
                        if config.brake_pressed_axis == -1.0
                        else "EXPLICIT_ENDPOINT"
                    ),
                },
            )
            return adapter
        except (pygame.error, OSError, RuntimeError, ValueError) as error:
            if selected is not None:
                try:
                    selected.quit()
                except (pygame.error, OSError, RuntimeError) as cleanup_error:
                    append_error_note(
                        error,
                        f"Secondary TS-XW device cleanup failure: {cleanup_error}",
                    )
            if owns_subsystem:
                try:
                    pygame.joystick.quit()
                except (pygame.error, OSError, RuntimeError) as cleanup_error:
                    append_error_note(
                        error,
                        f"Secondary joystick subsystem cleanup failure: {cleanup_error}",
                    )
            if not isinstance(error, pygame.error):
                raise
            failure = TsxwDeviceError(str(error))
            for note in getattr(error, "__notes__", ()):
                append_error_note(failure, note)
            raise failure from error

    def poll(self, events: Sequence[pygame.event.Event] = ()) -> TsxwInputSample:
        try:
            if any(
                event.type == pygame.JOYDEVICEREMOVED
                and event.dict.get("instance_id") == self._instance_id
                for event in events
            ):
                raise TsxwDeviceError("TS-XW device removed")
            if not self._joystick.get_init():
                raise TsxwDeviceError("TS-XW device disconnected")
            if self._joystick.get_instance_id() != self._instance_id:
                raise TsxwDeviceError("TS-XW instance changed")
            raw_steering = self._joystick.get_axis(STEERING_AXIS)
            raw_brake = self._joystick.get_axis(BRAKE_AXIS)
            raw_throttle = self._joystick.get_axis(THROTTLE_AXIS)
            if not all(
                isfinite(value) for value in (raw_steering, raw_brake, raw_throttle)
            ):
                raise TsxwDeviceError("TS-XW axis sample must be finite")
            buttons = (
                bool(self._joystick.get_button(LEFT_BUTTON)),
                bool(self._joystick.get_button(RIGHT_BUTTON)),
                bool(self._joystick.get_button(NOA_BUTTON)),
                bool(self._joystick.get_button(REJECT_BUTTON)),
            )
        except pygame.error as error:
            failure = TsxwDeviceError(f"TS-XW read failed: {error}")
            self._emit("tsxw_input_failure", {"reason": str(failure)})
            raise failure from error
        except TsxwDeviceError as error:
            self._emit("tsxw_input_failure", {"reason": str(error)})
            raise
        previous = self._previous_buttons
        self._previous_buttons = buttons
        steering = normalize_steering(raw_steering, self.config)
        return TsxwInputSample(
            raw_steering=raw_steering,
            raw_brake=raw_brake,
            raw_throttle=raw_throttle,
            steering=steering,
            brake=normalize_brake_pedal(
                raw_brake, pressed_axis=self.config.brake_pressed_axis
            ),
            throttle=normalize_pedal(raw_throttle, pressed_axis=-1.0),
            steering_engaged=steering != 0.0,
            left_pressed=buttons[0] and not previous[0],
            right_pressed=buttons[1] and not previous[1],
            noa_pressed=buttons[2] and not previous[2],
            reject_pressed=buttons[3] and not previous[3],
            left_held=buttons[0],
            right_held=buttons[1],
            noa_held=buttons[2],
            reject_held=buttons[3],
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._joystick.quit()
        finally:
            if self._owns_subsystem:
                pygame.joystick.quit()
        self._emit("tsxw_device_closed", {"instance_id": self._instance_id})

    def _emit(
        self, event_type: str, payload: dict[str, str | int | float | None]
    ) -> None:
        if self._event_sink is not None:
            self._event_sink(
                TsxwLogEvent(
                    event_type,
                    json.dumps(
                        payload, allow_nan=False, separators=(",", ":"), sort_keys=True
                    ),
                )
            )
