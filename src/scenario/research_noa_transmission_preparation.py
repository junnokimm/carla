from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import assert_never

import carla

from src.scenario.research_noa_types import ResearchNoAVehicle, ResearchNoAWorld
from src.vehicle.speed import calculate_speed_kmh


class TransmissionPreparationFailure(StrEnum):
    REVERSE = "reverse"
    INVALID_GEAR = "invalid-gear"
    TIMEOUT = "timeout"
    FRAME = "frame"
    ACTOR = "actor"
    SPEED = "speed"
    CONTROL = "control"
    CONFIRMATION_TIMEOUT = "confirmation-timeout"
    ENGAGEMENT = "engagement"
    AUTOMATIC_ENGAGEMENT = "automatic-engagement"


@dataclass(frozen=True, slots=True)
class TransmissionPreparationError(RuntimeError):
    failure: TransmissionPreparationFailure
    frame: int | None = None
    speed_kmh: float | None = None
    post_gear: int | None = None

    def __str__(self) -> str:
        match self.failure:
            case TransmissionPreparationFailure.REVERSE:
                return "transmission preparation observed reverse control"
            case TransmissionPreparationFailure.INVALID_GEAR:
                return (
                    f"transmission preparation observed invalid gear {self.post_gear}"
                )
            case TransmissionPreparationFailure.TIMEOUT:
                return "transmission preparation timed out without a stationary active frame"
            case TransmissionPreparationFailure.FRAME:
                return f"transmission preparation received non-increasing frame {self.frame}"
            case TransmissionPreparationFailure.ACTOR:
                return f"transmission preparation frame {self.frame} omitted the owned vehicle"
            case TransmissionPreparationFailure.SPEED:
                return f"transmission preparation received non-finite speed {self.speed_kmh!r} at frame {self.frame}"
            case TransmissionPreparationFailure.CONTROL:
                return "transmission preparation control could not be applied"
            case TransmissionPreparationFailure.CONFIRMATION_TIMEOUT:
                return "transmission manual gear confirmation timed out"
            case TransmissionPreparationFailure.ENGAGEMENT:
                return f"transmission manual prime observed gear {self.post_gear}"
            case TransmissionPreparationFailure.AUTOMATIC_ENGAGEMENT:
                return f"transmission automatic forward readback was not confirmed; observed gear {self.post_gear}"
            case unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class TransmissionPreparationResult:
    speed_kmh: float
    automatic_forward_gear: int | None


@dataclass(frozen=True, slots=True)
class TransmissionPreparationContext:
    bounded_brake: float
    timeout_seconds: float
    stationary_threshold_kmh: float
    monotonic_clock: Callable[[], float]


def prepare_moving_gear_zero(
    vehicle: ResearchNoAVehicle,
    world: ResearchNoAWorld,
    context: TransmissionPreparationContext,
) -> TransmissionPreparationResult:
    if not isfinite(context.timeout_seconds) or context.timeout_seconds <= 0.0:
        raise TransmissionPreparationError(TransmissionPreparationFailure.TIMEOUT)
    try:
        previous_frame: int | None = int(world.get_snapshot().frame)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise TransmissionPreparationError(
            TransmissionPreparationFailure.FRAME
        ) from error
    try:
        vehicle.apply_control(
            carla.VehicleControl(
                throttle=0.0,
                brake=context.bounded_brake,
                steer=0.0,
                hand_brake=False,
                reverse=False,
                manual_gear_shift=False,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise TransmissionPreparationError(
            TransmissionPreparationFailure.CONTROL
        ) from error

    deadline = context.monotonic_clock() + context.timeout_seconds
    while True:
        remaining = deadline - context.monotonic_clock()
        if remaining <= 0.0:
            raise TransmissionPreparationError(TransmissionPreparationFailure.TIMEOUT)
        try:
            snapshot = world.wait_for_tick(remaining)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.TIMEOUT
            ) from error

        frame = int(snapshot.frame)
        if previous_frame is not None and frame <= previous_frame:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.FRAME, frame=frame
            )
        previous_frame = frame
        try:
            actor = snapshot.find(vehicle.id)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.ACTOR,
                frame=frame,
            ) from error
        if actor is None:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.ACTOR, frame=frame
            )
        try:
            velocity = actor.get_velocity()
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.SPEED,
                frame=frame,
            ) from error
        speed_kmh = calculate_speed_kmh(velocity.x, velocity.y, velocity.z)
        if not isfinite(speed_kmh):
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.SPEED,
                frame=frame,
                speed_kmh=speed_kmh,
            )

        try:
            control = vehicle.get_control()
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.CONTROL,
                frame=frame,
            ) from error
        gear = int(control.gear)
        if control.reverse:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.REVERSE,
                frame=frame,
                speed_kmh=speed_kmh,
                post_gear=gear,
            )
        if gear < 0:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.INVALID_GEAR,
                frame=frame,
                speed_kmh=speed_kmh,
                post_gear=gear,
            )
        if gear > 0:
            if control.manual_gear_shift:
                raise TransmissionPreparationError(
                    TransmissionPreparationFailure.AUTOMATIC_ENGAGEMENT,
                    frame=frame,
                    speed_kmh=speed_kmh,
                    post_gear=gear,
                )
            if speed_kmh <= context.stationary_threshold_kmh:
                return TransmissionPreparationResult(speed_kmh, gear)
        if speed_kmh <= context.stationary_threshold_kmh:
            return TransmissionPreparationResult(speed_kmh, None)
