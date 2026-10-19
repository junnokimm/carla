"""Bounded preloaded overlap playback on the existing Pygame mixer thread.

No application worker thread, runtime PCM creation, RPC, file I/O or music use.
Finite grains include their own fade tail, even if the Python loop stops entirely.
"""

from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Any, ClassVar

from src.scenario.vehicle_audio_assets import (
    COHORTS,
    HOP_SECONDS,
    VOICE_COUNT,
    VOICES_PER_COHORT,
    AudioBank,
    procedural_bank,
    voice_mix,
)
from src.scenario.vehicle_audio_model import SoundParameters


class VehicleAudioPlayback:
    _owner: ClassVar[VehicleAudioPlayback | None] = None

    def __init__(
        self,
        mixer: Any,
        *,
        bank_provider: Callable[[int, int], AudioBank] = procedural_bank,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self.mixer = mixer
        self.bank_provider = bank_provider
        self.wait = wait
        self.channels: list[Any] = []
        self.sounds: list[Any] = []
        self.format: tuple[int, int, int] | None = None
        self.available = False
        self.prepared = False
        self.closed = False
        self.error: str | None = None
        self.cleanup_errors: list[str] = []
        self.launches = 0
        self.skipped_busy = 0
        self._cohort = 0
        self._next_at = 0.0
        self._phase_origin: float | None = None

    def prepare(self) -> None:
        if self.prepared or self.closed:
            return
        self.prepared = True
        try:
            if VehicleAudioPlayback._owner is not None:
                raise RuntimeError("vehicle audio mixer already owned")
            config = self.mixer.get_init()
            if config is None:
                self.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
                config = self.mixer.get_init()
            if config is None or config[1] != -16:
                raise ValueError("vehicle audio requires signed 16-bit PCM")
            self.format = config
            bank = self.bank_provider(config[0], config[2])
            count = self.mixer.get_num_channels()
            # Do not steal any existing sound. Current P8 has no other reservation owner.
            if any(
                self.mixer.Channel(i).get_busy() for i in range(min(count, VOICE_COUNT))
            ):
                raise RuntimeError("vehicle audio channel range is already in use")
            self.mixer.set_num_channels(max(count, VOICE_COUNT + 8))
            VehicleAudioPlayback._owner = self
            if self.mixer.set_reserved(VOICE_COUNT) != VOICE_COUNT:
                raise RuntimeError("vehicle audio channel reservation failed")
            self.channels = [self.mixer.Channel(i) for i in range(VOICE_COUNT)]
            self.sounds = [self.mixer.Sound(buffer=pcm) for pcm in bank.pcm]
            self.available = True
        except Exception as error:  # noqa: BLE001 -- optional audio must not abort driving
            self.error = f"{type(error).__name__}: {error}"
            self.close(fade=False)

    def update(self, parameters: SoundParameters, now: float) -> None:
        if not self.available or self.closed:
            return
        try:
            if self.mixer.get_init() != self.format:
                raise RuntimeError("mixer was closed or its format changed")
            if not parameters.fresh or now < self._next_at:
                return
            # Never catch up with a burst of sounds after a stalled frame.
            self._next_at = max(self._next_at + HOP_SECONDS, now + HOP_SECONDS * 0.5)
            start = self._cohort * VOICES_PER_COHORT
            if any(
                self.channels[start + i].get_busy() for i in range(VOICES_PER_COHORT)
            ):
                self.skipped_busy += 1
                return
            if self._phase_origin is None:
                self._phase_origin = now
            for i, (asset, gain) in enumerate(
                voice_mix(parameters, now - self._phase_origin)
            ):
                if gain <= 0.0001:
                    continue
                channel = self.channels[start + i]
                # Embedded zero-slope head/tail; finite duration protects against stalls.
                channel.play(self.sounds[asset], loops=0)
                channel.set_volume(gain)
            self._cohort = (self._cohort + 1) % COHORTS
            self.launches += 1
        except Exception as error:  # noqa: BLE001 -- isolate backend failures from control
            self.error = f"{type(error).__name__}: {error}"
            self.close(fade=False)

    def close(self, *, fade: bool = True) -> None:
        if self.closed:
            return
        self.closed = True
        self.available = False
        # Only owned channels; never mixer.stop/quit or Sound.stop on click assets.
        if VehicleAudioPlayback._owner is self:
            if fade and self.channels:
                for channel in self.channels:
                    self._cleanup(lambda channel=channel: channel.fadeout(50))
                # Exit only, after input cleanup, before existing pygame.quit destroys mixer.
                self._cleanup(lambda: self.wait(0.05))
            for channel in self.channels:
                self._cleanup(channel.stop)
            self._cleanup(lambda: self.mixer.set_reserved(0))
            VehicleAudioPlayback._owner = None
        self.channels.clear()
        self.sounds.clear()

    def _cleanup(self, action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as error:  # noqa: BLE001 -- preserve primary driving/shutdown error
            self.cleanup_errors.append(f"{type(error).__name__}: {error}")
