"""Smoothed mixing of actual cabin recordings, using observations only."""

from dataclasses import dataclass
from math import expm1, isfinite

from src.scenario.vehicle_audio_model import VehicleAudioState, clamp, valid_state

MASTER_GAIN = 0.55


@dataclass(frozen=True, slots=True)
class RecordedSoundParameters:
    gains: tuple[float, float, float]  # idle, acceleration, cruise
    fresh: bool


class RecordedEngineSoundModel:
    def __init__(self):
        self._last_at = None
        self._previous = None
        self._acceleration = 0.0
        self._gains = [0.0, 0.0, 0.0]

    def update(self, state: VehicleAudioState | None, now: float):
        fresh = valid_state(state, now)
        if not isfinite(now):
            return RecordedSoundParameters((0.0, 0.0, 0.0), False)
        dt = 0.0 if self._last_at is None else clamp(now - self._last_at, 0, 0.1)
        self._last_at = now
        target = (0.0, 0.0, 0.0)
        if fresh and state is not None and state.engine_running:
            if self._previous is not None:
                stamp, speed = self._previous
                interval = state.observed_at - stamp
                if 0 < interval <= 0.5:
                    acceleration = clamp(
                        (state.speed_kmh - speed) / (3.6 * interval), -5, 5
                    )
                    self._acceleration += (acceleration - self._acceleration) * (
                        -expm1(-interval / 0.4)
                    )
                elif interval > 0.5:
                    self._acceleration = 0.0
            self._previous = (state.observed_at, state.speed_kmh)
            moving = clamp(state.speed_kmh / 15.0)
            demand = clamp((state.throttle - 0.08) / 0.72) * (1 - clamp(state.brake))
            rising = clamp(self._acceleration / 1.2)
            idle = (1 - moving) * (1 - 0.65 * demand)
            accelerating = (1 - moving) * 0.65 * demand + moving * demand * (
                0.2 + 0.8 * rising
            )
            # The highway recording includes road/wind noise: attenuate it at
            # lower speeds instead of playing a 100 km/h cabin at full gain.
            road_level = 0.30 + 0.70 * clamp(state.speed_kmh / 100.0)
            target = (
                idle,
                accelerating,
                max(0.0, 1 - idle - accelerating) * road_level,
            )
        else:
            self._previous = None
            self._acceleration = 0.0
        # Manual/NoA changes do not restart the recording or reset its gain.
        alpha = -expm1(-dt / (0.3 if fresh else 0.08))
        for index, weight in enumerate(target):
            self._gains[index] += (MASTER_GAIN * weight - self._gains[index]) * alpha
        return RecordedSoundParameters(tuple(self._gains), fresh)
