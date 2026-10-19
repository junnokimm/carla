"""Share a real telemetry light observation with audio in the same drive loop.

This never supplies telemetry, input decisions, or hazard judgments. A missing,
expired, or previous-loop sample requires the original fresh audio RPC.
"""

from collections.abc import Callable
from time import perf_counter

import carla


class IterationLightState:
    def __init__(self, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self._active = False
        self._sample: tuple[carla.VehicleLightState, float] | None = None

    def begin(self) -> None:
        self._active = True
        self._sample = None

    def invalidate(self) -> None:
        self._sample = None

    def publish(self, lights: carla.VehicleLightState) -> None:
        if self._active:
            self._sample = (lights, self._clock())

    def consume(self) -> tuple[carla.VehicleLightState, float] | None:
        sample = self._sample
        self._sample = None
        self._active = False
        if sample is None:
            return None
        lights, captured = sample
        age = self._clock() - captured
        # A slow scene callback must not make audio consume an old observation.
        return (lights, age) if 0.0 <= age <= 0.050 else None
