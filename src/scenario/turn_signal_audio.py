from __future__ import annotations

import math
from array import array
from typing import Final

import pygame

TURN_SIGNAL_CLICK_INTERVAL_SECONDS: Final = 0.8
_CLICK_DURATION_SECONDS: Final = 0.025
_CLICK_FREQUENCY_HZ: Final = 1_800.0
_CLICK_AMPLITUDE: Final = 12_000
_DEFAULT_SAMPLE_RATE: Final = 22_050


class TurnSignalAudio:
    def __init__(self) -> None:
        self._sound: pygame.mixer.Sound | None = None
        self._next_click_at: float | None = None
        self._available = True
        self._closed = False

    def update(self, *, active: bool, now: float) -> None:
        if self._closed:
            return
        if not active:
            self._next_click_at = None
            return
        if self._next_click_at is not None and now < self._next_click_at:
            return
        sound = self._load_sound()
        if sound is not None:
            try:
                sound.play()
            except pygame.error:
                self._sound = None
                self._available = False
        self._next_click_at = now + TURN_SIGNAL_CLICK_INTERVAL_SECONDS

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._next_click_at = None
        sound = self._sound
        self._sound = None
        if sound is not None:
            try:
                sound.stop()
            except pygame.error:
                self._available = False

    def _load_sound(self) -> pygame.mixer.Sound | None:
        if self._sound is not None or not self._available:
            return self._sound
        try:
            mixer_config = pygame.mixer.get_init()
            if mixer_config is None:
                pygame.mixer.init(
                    frequency=_DEFAULT_SAMPLE_RATE,
                    size=-16,
                    channels=1,
                    buffer=256,
                )
                mixer_config = (_DEFAULT_SAMPLE_RATE, -16, 1)
            sample_rate, _, channels = mixer_config
            sample_count = round(sample_rate * _CLICK_DURATION_SECONDS)
            samples = array("h")
            for index in range(sample_count):
                envelope = 1.0 - index / sample_count
                sample = round(
                    _CLICK_AMPLITUDE
                    * envelope
                    * math.sin(math.tau * _CLICK_FREQUENCY_HZ * index / sample_rate)
                )
                samples.extend((sample,) * channels)
            self._sound = pygame.mixer.Sound(buffer=samples.tobytes())
        except pygame.error:
            self._available = False
        return self._sound
