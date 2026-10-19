"""Generic cabin sound parameters, not measured Mercedes engine physics."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, expm1, isfinite

STALE_SECONDS = 0.5
IDLE_RPM = 750.0
MAX_RPM = 6500.0  # Sound-model limit, NOT VehiclePhysicsControl.max_rpm.


@dataclass(frozen=True, slots=True)
class VehicleAudioState:
    speed_kmh: float
    throttle: float
    brake: float
    gear: int | None
    reverse: bool
    observed_at: float  # Host monotonic seconds, original snapshot acquisition.
    actor_id: str
    stage_id: str
    mode: str
    measured_rpm: float | None = None
    engine_running: bool = True  # CARLA has no ignition observation here; assumption.


@dataclass(frozen=True, slots=True)
class SoundParameters:
    rpm: float
    rpm_source: str
    engine_load: float
    engine_pitch: float
    engine_volume: float
    idle_blend: float
    acceleration_blend: float
    cruise_blend: float
    deceleration_blend: float
    gear_shift_transition: float
    road_gain: float
    wind_gain: float
    fresh: bool


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def valid_state(state: VehicleAudioState | None, now: float) -> bool:
    if state is None or not isfinite(now):
        return False
    return (
        all(
            isinstance(v, (int, float)) and isfinite(v)
            for v in (state.speed_kmh, state.throttle, state.brake, state.observed_at)
        )
        and state.speed_kmh >= 0
        and state.observed_at >= 0
        and 0 <= now - state.observed_at <= STALE_SECONDS
    )


def usable_gear(gear: int | None) -> int | None:
    return gear if type(gear) is int and -1 <= gear <= 8 else None


class EngineSoundModel:
    def __init__(self) -> None:
        self._rpm = IDLE_RPM
        self._load = 0.0
        self._speed = 0.0
        self._gain = 0.0
        self._last_at: float | None = None
        self._identity: tuple[str, str] | None = None
        self._gear: int | None = None
        self._shift_at = float("-inf")

    def update(self, state: VehicleAudioState | None, now: float) -> SoundParameters:
        fresh = valid_state(state, now)
        if not isfinite(now):
            now = self._last_at if self._last_at is not None else 0.0
        dt = 0.0 if self._last_at is None else max(0.0, now - self._last_at)
        # A mode transition is deliberately NOT an identity change.
        identity = None if state is None else (state.actor_id, state.stage_id)
        if identity is not None and identity != self._identity:
            self._rpm, self._load, self._speed, self._gain = IDLE_RPM, 0.0, 0.0, 0.0
            self._gear = None
            self._shift_at = float("-inf")
            self._identity = identity
            dt = 0.0
        self._last_at = max(now, self._last_at if self._last_at is not None else now)
        target_rpm, target_load, speed, brake, throttle = IDLE_RPM, 0.0, 0.0, 0.0, 0.0
        rpm_source = "estimated_rpm"
        running = False
        if fresh and state is not None:
            speed = min(state.speed_kmh, 300.0)
            throttle, brake = clamp(state.throttle), clamp(state.brake)
            gear = usable_gear(state.gear)
            if gear is not None and self._gear is not None and gear != self._gear:
                self._shift_at = now
            self._gear = gear
            running = state.engine_running
            target_load = clamp(throttle * (1.0 - 0.85 * brake))
            if gear == 0 and not state.reverse:
                target_rpm = IDLE_RPM + 4000.0 * throttle
            else:
                # Generic acoustic gearing and tyre circumference, not blueprint calibration.
                ratios = (3.8, 2.4, 1.6, 1.18, 0.92, 0.76, 0.65, 0.58)
                fallback = min(7, int(speed / 22.0))
                ratio = (
                    3.5
                    if state.reverse or gear == -1
                    else ratios[fallback if gear is None else gear - 1]
                )
                wheel_rpm = speed / 3.6 / 2.1 * 60.0
                target_rpm = max(IDLE_RPM, wheel_rpm * ratio * 3.2)
                target_rpm += throttle * 650.0 * (1.0 - clamp(speed / 25.0))
            if (
                isinstance(state.measured_rpm, (int, float))
                and isfinite(state.measured_rpm)
                and state.measured_rpm >= 0
            ):
                target_rpm = state.measured_rpm
                rpm_source = "measured_rpm"
        shift = exp(-max(0.0, now - self._shift_at) / 0.18)
        target_rpm = clamp(target_rpm, IDLE_RPM, MAX_RPM)
        alpha = -expm1(-dt / 0.18)
        delta = clamp((target_rpm - self._rpm) * alpha, -5000 * dt, 6500 * dt)
        self._rpm += delta
        self._load += (target_load * (1.0 - 0.35 * shift) - self._load) * alpha
        self._speed += (speed - self._speed) * (-expm1(-dt / 0.25))
        self._gain += (float(fresh) - self._gain) * (-expm1(-dt / 0.12))
        idle = 1.0 - clamp(self._speed / 8.0)
        decel = (1.0 - idle) * clamp(brake + (0.65 if throttle < 0.08 else 0.0))
        accel = (1.0 - idle) * self._load * (1.0 - decel)
        cruise = max(0.0, 1.0 - idle - decel - accel)
        return SoundParameters(
            self._rpm,
            rpm_source,
            self._load,
            self._rpm / 1500.0,
            self._gain
            * (0.12 + 0.24 * self._load + 0.05 * self._rpm / MAX_RPM)
            * float(running),
            idle,
            accel,
            cruise,
            decel,
            shift,
            self._gain * 0.22 * clamp(self._speed / 120.0) ** 1.15,
            self._gain * 0.18 * clamp(self._speed / 140.0) ** 2,
            fresh,
        )
