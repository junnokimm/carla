from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Final, assert_never

import carla

from src.scenario.research_noa_types import ResearchNoAVehicle, ResearchNoAWorld

TRANSMISSION_PRIME_STATIONARY_THRESHOLD_KMH: Final = 0.1
TRANSMISSION_PRIME_CONFIRMATION_MIN_SECONDS: Final = 0.5
TRANSMISSION_PRIME_CONFIRMATION_MAX_SECONDS: Final = 1.0
TRANSMISSION_PRIME_GEAR_SWITCH_MULTIPLIER: Final = 2.0

type MonotonicClock = Callable[[], float]


class ResearchTransmissionPrimeFailure(StrEnum):
    REVERSE = "reverse"
    INVALID_GEAR = "invalid-gear"
    MOVING = "moving"
    INVALID_GEAR_SWITCH_TIME = "invalid-gear-switch-time"
    CONFIRMATION_TIMEOUT = "confirmation-timeout"
    ENGAGEMENT = "engagement"


class ResearchNoATransmissionPrimeError(RuntimeError):
    def __init__(
        self,
        failure: ResearchTransmissionPrimeFailure,
        *,
        initial_gear: int,
        actual_speed_kmh: float,
        post_prime_gear: int | None = None,
        gear_switch_time_seconds: float | None = None,
    ) -> None:
        self.failure = failure
        self.initial_gear = initial_gear
        self.actual_speed_kmh = actual_speed_kmh
        self.post_prime_gear = post_prime_gear
        self.gear_switch_time_seconds = gear_switch_time_seconds
        super().__init__(str(self))

    def __str__(self) -> str:
        match self.failure:
            case ResearchTransmissionPrimeFailure.REVERSE:
                return "research live-smoke transmission prime requires forward control"
            case ResearchTransmissionPrimeFailure.INVALID_GEAR:
                return (
                    f"research live-smoke cannot prime initial gear {self.initial_gear}"
                )
            case ResearchTransmissionPrimeFailure.MOVING:
                return (
                    "research live-smoke cannot prime gear 0 while moving at "
                    f"{self.actual_speed_kmh} km/h"
                )
            case ResearchTransmissionPrimeFailure.INVALID_GEAR_SWITCH_TIME:
                return (
                    "research live-smoke transmission prime requires a finite, "
                    "non-negative gear_switch_time; observed "
                    f"{self.gear_switch_time_seconds!r}"
                )
            case ResearchTransmissionPrimeFailure.CONFIRMATION_TIMEOUT:
                return "research live-smoke transmission prime confirmation timed out"
            case ResearchTransmissionPrimeFailure.ENGAGEMENT:
                return (
                    "research live-smoke transmission prime did not engage gear 1; "
                    f"observed gear {self.post_prime_gear}"
                )
            case unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class ResearchTransmissionPrimeTelemetry:
    initial_gear: int
    required: bool
    applied: bool
    post_prime_gear: int


@dataclass(frozen=True, slots=True)
class TransmissionPrimeContext:
    bounded_brake: float
    actual_speed_kmh: float
    monotonic_clock: MonotonicClock = monotonic


def prime_live_transmission(
    vehicle: ResearchNoAVehicle,
    world: ResearchNoAWorld,
    context: TransmissionPrimeContext,
) -> ResearchTransmissionPrimeTelemetry:
    initial_control = vehicle.get_control()
    initial_gear = int(initial_control.gear)
    if initial_control.reverse:
        raise ResearchNoATransmissionPrimeError(
            ResearchTransmissionPrimeFailure.REVERSE,
            initial_gear=initial_gear,
            actual_speed_kmh=context.actual_speed_kmh,
        )
    if initial_gear > 0:
        return ResearchTransmissionPrimeTelemetry(
            initial_gear=initial_gear,
            required=False,
            applied=False,
            post_prime_gear=initial_gear,
        )
    if initial_gear < 0:
        raise ResearchNoATransmissionPrimeError(
            ResearchTransmissionPrimeFailure.INVALID_GEAR,
            initial_gear=initial_gear,
            actual_speed_kmh=context.actual_speed_kmh,
        )
    if context.actual_speed_kmh > TRANSMISSION_PRIME_STATIONARY_THRESHOLD_KMH:
        raise ResearchNoATransmissionPrimeError(
            ResearchTransmissionPrimeFailure.MOVING,
            initial_gear=initial_gear,
            actual_speed_kmh=context.actual_speed_kmh,
        )
    gear_switch_time = float(vehicle.get_physics_control().gear_switch_time)
    if not isfinite(gear_switch_time) or gear_switch_time < 0.0:
        raise ResearchNoATransmissionPrimeError(
            ResearchTransmissionPrimeFailure.INVALID_GEAR_SWITCH_TIME,
            initial_gear=initial_gear,
            actual_speed_kmh=context.actual_speed_kmh,
            gear_switch_time_seconds=gear_switch_time,
        )
    confirmation_window = min(
        max(
            gear_switch_time * TRANSMISSION_PRIME_GEAR_SWITCH_MULTIPLIER,
            TRANSMISSION_PRIME_CONFIRMATION_MIN_SECONDS,
        ),
        TRANSMISSION_PRIME_CONFIRMATION_MAX_SECONDS,
    )
    deadline = context.monotonic_clock() + confirmation_window
    vehicle.apply_control(
        carla.VehicleControl(
            throttle=0.0,
            brake=context.bounded_brake,
            steer=0.0,
            hand_brake=False,
            reverse=False,
            manual_gear_shift=True,
            gear=1,
        )
    )
    while True:
        post_prime_gear = int(vehicle.get_control().gear)
        if post_prime_gear == 1:
            return ResearchTransmissionPrimeTelemetry(
                initial_gear=initial_gear,
                required=True,
                applied=True,
                post_prime_gear=post_prime_gear,
            )
        if post_prime_gear != 0:
            raise ResearchNoATransmissionPrimeError(
                ResearchTransmissionPrimeFailure.ENGAGEMENT,
                initial_gear=initial_gear,
                actual_speed_kmh=context.actual_speed_kmh,
                post_prime_gear=post_prime_gear,
            )
        remaining = deadline - context.monotonic_clock()
        if remaining <= 0.0:
            raise ResearchNoATransmissionPrimeError(
                ResearchTransmissionPrimeFailure.CONFIRMATION_TIMEOUT,
                initial_gear=initial_gear,
                actual_speed_kmh=context.actual_speed_kmh,
                post_prime_gear=post_prime_gear,
            )
        try:
            world.wait_for_tick(remaining)
        except RuntimeError as error:
            raise ResearchNoATransmissionPrimeError(
                ResearchTransmissionPrimeFailure.CONFIRMATION_TIMEOUT,
                initial_gear=initial_gear,
                actual_speed_kmh=context.actual_speed_kmh,
                post_prime_gear=post_prime_gear,
            ) from error
