from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Final, assert_never

from src.scenario.research_noa_control_delivery import ControlSender
from src.scenario.research_noa_transmission_confirmation import (
    StationaryPrimeContext,
    prime_stationary_gear_zero,
)
from src.scenario.research_noa_transmission_preparation import (
    TransmissionPreparationContext,
    TransmissionPreparationError,
    TransmissionPreparationFailure,
    prepare_moving_gear_zero,
)
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
    PREPARATION_TIMEOUT = "preparation-timeout"
    PREPARATION_FRAME = "preparation-frame"
    PREPARATION_ACTOR = "preparation-actor"
    PREPARATION_SPEED = "preparation-speed"
    PREPARATION_CONTROL = "preparation-control"
    AUTOMATIC_ENGAGEMENT = "automatic-engagement"


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
            case ResearchTransmissionPrimeFailure.PREPARATION_TIMEOUT:
                return "research live-smoke transmission preparation timed out"
            case ResearchTransmissionPrimeFailure.PREPARATION_FRAME:
                return (
                    "research live-smoke transmission preparation frame did not advance"
                )
            case ResearchTransmissionPrimeFailure.PREPARATION_ACTOR:
                return "research live-smoke transmission preparation lost the owned vehicle"
            case ResearchTransmissionPrimeFailure.PREPARATION_SPEED:
                return "research live-smoke preparation observed non-finite speed"
            case ResearchTransmissionPrimeFailure.PREPARATION_CONTROL:
                return "research live-smoke transmission preparation control failed"
            case ResearchTransmissionPrimeFailure.AUTOMATIC_ENGAGEMENT:
                return (
                    "research live-smoke transmission did not confirm automatic forward "
                    f"control; observed gear {self.post_prime_gear}"
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
    preparation_timeout_seconds: float | None = None
    control_sender: ControlSender | None = None


def _preparation_failure(
    error: TransmissionPreparationError,
) -> ResearchTransmissionPrimeFailure:
    match error.failure:
        case TransmissionPreparationFailure.REVERSE:
            return ResearchTransmissionPrimeFailure.REVERSE
        case TransmissionPreparationFailure.INVALID_GEAR:
            return ResearchTransmissionPrimeFailure.INVALID_GEAR
        case TransmissionPreparationFailure.TIMEOUT:
            return ResearchTransmissionPrimeFailure.PREPARATION_TIMEOUT
        case TransmissionPreparationFailure.FRAME:
            return ResearchTransmissionPrimeFailure.PREPARATION_FRAME
        case TransmissionPreparationFailure.ACTOR:
            return ResearchTransmissionPrimeFailure.PREPARATION_ACTOR
        case TransmissionPreparationFailure.SPEED:
            return ResearchTransmissionPrimeFailure.PREPARATION_SPEED
        case TransmissionPreparationFailure.CONTROL:
            return ResearchTransmissionPrimeFailure.PREPARATION_CONTROL
        case TransmissionPreparationFailure.CONFIRMATION_TIMEOUT:
            return ResearchTransmissionPrimeFailure.CONFIRMATION_TIMEOUT
        case TransmissionPreparationFailure.ENGAGEMENT:
            return ResearchTransmissionPrimeFailure.ENGAGEMENT
        case TransmissionPreparationFailure.AUTOMATIC_ENGAGEMENT:
            return ResearchTransmissionPrimeFailure.AUTOMATIC_ENGAGEMENT
        case unreachable:
            assert_never(unreachable)


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
        if initial_control.manual_gear_shift:
            raise ResearchNoATransmissionPrimeError(
                ResearchTransmissionPrimeFailure.AUTOMATIC_ENGAGEMENT,
                initial_gear=initial_gear,
                actual_speed_kmh=context.actual_speed_kmh,
                post_prime_gear=initial_gear,
            )
        return ResearchTransmissionPrimeTelemetry(
            initial_gear, False, False, initial_gear
        )
    if initial_gear < 0:
        raise ResearchNoATransmissionPrimeError(
            ResearchTransmissionPrimeFailure.INVALID_GEAR,
            initial_gear=initial_gear,
            actual_speed_kmh=context.actual_speed_kmh,
        )
    actual_speed_kmh = context.actual_speed_kmh
    if actual_speed_kmh > TRANSMISSION_PRIME_STATIONARY_THRESHOLD_KMH:
        timeout = context.preparation_timeout_seconds
        if timeout is None:
            raise ResearchNoATransmissionPrimeError(
                ResearchTransmissionPrimeFailure.MOVING,
                initial_gear=initial_gear,
                actual_speed_kmh=actual_speed_kmh,
            )
        try:
            preparation = prepare_moving_gear_zero(
                vehicle,
                world,
                TransmissionPreparationContext(
                    bounded_brake=context.bounded_brake,
                    timeout_seconds=timeout,
                    stationary_threshold_kmh=TRANSMISSION_PRIME_STATIONARY_THRESHOLD_KMH,
                    monotonic_clock=context.monotonic_clock,
                    control_sender=context.control_sender,
                ),
            )
        except TransmissionPreparationError as error:
            raise ResearchNoATransmissionPrimeError(
                _preparation_failure(error),
                initial_gear=initial_gear,
                actual_speed_kmh=actual_speed_kmh
                if error.speed_kmh is None
                else error.speed_kmh,
            ) from error.__cause__
        actual_speed_kmh = preparation.speed_kmh
        if preparation.automatic_forward_gear is not None:
            return ResearchTransmissionPrimeTelemetry(
                initial_gear, False, False, preparation.automatic_forward_gear
            )
    gear_switch_time = float(vehicle.get_physics_control().gear_switch_time)
    if not isfinite(gear_switch_time) or gear_switch_time < 0.0:
        raise ResearchNoATransmissionPrimeError(
            ResearchTransmissionPrimeFailure.INVALID_GEAR_SWITCH_TIME,
            initial_gear=initial_gear,
            actual_speed_kmh=actual_speed_kmh,
            gear_switch_time_seconds=gear_switch_time,
        )
    confirmation_window = min(
        max(
            gear_switch_time * TRANSMISSION_PRIME_GEAR_SWITCH_MULTIPLIER,
            TRANSMISSION_PRIME_CONFIRMATION_MIN_SECONDS,
        ),
        TRANSMISSION_PRIME_CONFIRMATION_MAX_SECONDS,
    )
    try:
        post_prime_gear = prime_stationary_gear_zero(
            vehicle,
            world,
            StationaryPrimeContext(
                bounded_brake=context.bounded_brake,
                actual_speed_kmh=actual_speed_kmh,
                confirmation_window_seconds=confirmation_window,
                monotonic_clock=context.monotonic_clock,
                control_sender=context.control_sender,
            ),
        )
    except TransmissionPreparationError as error:
        raise ResearchNoATransmissionPrimeError(
            _preparation_failure(error),
            initial_gear=initial_gear,
            actual_speed_kmh=actual_speed_kmh,
            post_prime_gear=error.post_gear,
        ) from error.__cause__
    return ResearchTransmissionPrimeTelemetry(initial_gear, True, True, post_prime_gear)
