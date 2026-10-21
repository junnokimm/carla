"""Finite, best-effort warning cue shared by training and the M2 warning.

PCM is prepared before driving, never inside the display/control loop. Own only
this Sound: use free, unreserved mixer channels and never steal/reset others.
"""

from array import array
from functools import lru_cache
from math import sin, tau
from time import perf_counter_ns

import pygame

from src.scenario.recorded_vehicle_audio import duck_vehicle_audio_for_warning

CUE_ID = "hazard-three-beeps-v1"
TONE_SECONDS = 0.16
GAP_SECONDS = 0.10
DURATION_SECONDS = 3 * TONE_SECONDS + 2 * GAP_SECONDS


@lru_cache(maxsize=8)
def warning_pcm(rate, channels):
    if not 8000 <= rate <= 192000 or channels not in (1, 2):
        raise ValueError("unsupported warning audio format")
    samples = array("h")
    for index in range(round(rate * DURATION_SECONDS)):
        t = index / rate
        pulse = int(t / (TONE_SECONDS + GAP_SECONDS))
        within = t - pulse * (TONE_SECONDS + GAP_SECONDS)
        envelope = max(0.0, min(1.0, within / .005, (TONE_SECONDS - within) / .005))
        value = round(6000 * envelope * sin(tau * 1200 * t))
        samples.extend((value,) * channels)
    return samples.tobytes()


class HazardWarningAudio:
    def __init__(self, *, mixer=None, clock_ns=None):
        self.mixer = pygame.mixer if mixer is None else mixer
        self.clock_ns = perf_counter_ns if clock_ns is None else clock_ns
        self.sound = None
        self.format = None
        self.preparation = "NOT_PREPARED"
        self.closed = False
        self.attempts = 0
        self.errors = []
        self.last_play = None

    def _error(self, operation, error):
        detail = f"{operation}:{type(error).__name__}:{error}"
        if detail not in self.errors and len(self.errors) < 16:
            self.errors.append(detail)

    def prepare(self):
        if self.closed or self.preparation != "NOT_PREPARED":
            return
        try:
            if self.mixer.get_init() is None:
                self.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
            self.format = self.mixer.get_init()
            rate, size, channels = self.format
            if size != -16:
                raise ValueError("warning audio requires signed 16-bit mixer")
            self.sound = self.mixer.Sound(buffer=warning_pcm(rate, channels))
            self.preparation = "READY"
        except Exception as error:  # noqa: BLE001 -- audio is never a driving requirement
            self.preparation = "FAILED"
            self._error("prepare", error)

    def play(self):
        """No PCM generation, initialization, sleeps, or channel stealing here."""
        self.attempts += 1
        result = {
            "cue_id": CUE_ID, "duration_s": DURATION_SECONDS,
            "attempt": self.attempts, "status": "UNAVAILABLE",
            "play_request_accepted": False, "physical_onset_measured": False,
            "physical_audibility_verified": False, "session_action": "CONTINUE",
            "request_started_perf_counter_ns": None,
            "request_completed_perf_counter_ns": None,
        }
        # Lower already-playing AND queued cabin recordings before the cue.
        # Keep this independent of playback so a ducking error cannot suppress it.
        try:
            result["vehicle_audio_ducking"] = duck_vehicle_audio_for_warning(self.mixer, DURATION_SECONDS)
        except Exception as error:  # noqa: BLE001 -- visual/audio warning still proceeds
            result["vehicle_audio_ducking"] = {"status": "FAILED", "reason": str(error)}
            self._error("duck", error)
        try:
            result["request_started_perf_counter_ns"] = self.clock_ns()
            if self.closed or self.sound is None:
                result["reason"] = "CLOSED" if self.closed else self.preparation
            elif self.mixer.get_init() != self.format:
                # SDL may be reinitialized between stages. Do not reuse stale
                # Sound data or reinitialize the device inside an active frame.
                result["reason"] = "MIXER_CHANGED_OR_CLOSED"
            elif self.sound.play() is None:
                result["reason"] = "NO_FREE_UNRESERVED_CHANNEL"
            else:
                result.update(status="PLAY_REQUEST_ACCEPTED", play_request_accepted=True)
            result["request_completed_perf_counter_ns"] = self.clock_ns()
        except Exception as error:  # noqa: BLE001 -- preserve the visual warning and control
            result.update(status="FAILED", reason=f"{type(error).__name__}:{error}")
            self._error("play", error)
        self.last_play = result
        return dict(result)

    def close(self):
        if self.closed:
            return
        self.closed = True
        sound, self.sound = self.sound, None
        try:
            if sound is not None:
                sound.stop()
        except Exception as error:  # noqa: BLE001 -- own finite cue can finish naturally
            self._error("close", error)

    def summary(self):
        return {
            "cue_id": CUE_ID, "duration_s": DURATION_SECONDS,
            "preparation": self.preparation, "mixer_format": self.format,
            "closed": self.closed, "attempts": self.attempts,
            "last_play": None if self.last_play is None else dict(self.last_play),
            "errors": list(self.errors), "session_action": "CONTINUE",
        }
