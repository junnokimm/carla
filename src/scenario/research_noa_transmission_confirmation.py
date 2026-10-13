from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import carla

from src.scenario.research_noa_control_delivery import ControlSender
from src.scenario.research_noa_transmission_preparation import (
    TransmissionPreparationError,
    TransmissionPreparationFailure,
)
from src.scenario.research_noa_types import ResearchNoAVehicle, ResearchNoAWorld


@dataclass(frozen=True, slots=True)
class StationaryPrimeContext:
    bounded_brake: float
    actual_speed_kmh: float
    confirmation_window_seconds: float
    monotonic_clock: Callable[[], float]
    control_sender: ControlSender | None = None


def _wait_for_fresh_tick(
    world: ResearchNoAWorld,
    remaining: float,
    previous_frame: int | None,
) -> int:
    frame = int(world.wait_for_tick(remaining).frame)
    if previous_frame is not None and frame <= previous_frame:
        raise TransmissionPreparationError(
            TransmissionPreparationFailure.FRAME, frame=frame
        )
    return frame


def prime_stationary_gear_zero(
    vehicle: ResearchNoAVehicle,
    world: ResearchNoAWorld,
    context: StationaryPrimeContext,
) -> int:
    post_gear = 0
    try:
        previous_frame: int | None = int(world.get_snapshot().frame)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise TransmissionPreparationError(
            TransmissionPreparationFailure.CONFIRMATION_TIMEOUT,
            speed_kmh=context.actual_speed_kmh,
            post_gear=post_gear,
        ) from error
    try:
        (context.control_sender or vehicle.apply_control)(
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
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise TransmissionPreparationError(
            TransmissionPreparationFailure.CONTROL
        ) from error

    deadline = context.monotonic_clock() + context.confirmation_window_seconds
    while True:
        remaining = deadline - context.monotonic_clock()
        if remaining <= 0.0:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.CONFIRMATION_TIMEOUT,
                speed_kmh=context.actual_speed_kmh,
                post_gear=post_gear,
            )
        try:
            previous_frame = _wait_for_fresh_tick(world, remaining, previous_frame)
            post_gear = int(vehicle.get_control().gear)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.CONFIRMATION_TIMEOUT,
                speed_kmh=context.actual_speed_kmh,
                post_gear=post_gear,
            ) from error
        if post_gear == 1:
            break
        if post_gear != 0:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.ENGAGEMENT,
                speed_kmh=context.actual_speed_kmh,
                post_gear=post_gear,
            )

    try:
        (context.control_sender or vehicle.apply_control)(
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
            TransmissionPreparationFailure.CONTROL,
            speed_kmh=context.actual_speed_kmh,
            post_gear=post_gear,
        ) from error

    deadline = context.monotonic_clock() + context.confirmation_window_seconds
    while True:
        remaining = deadline - context.monotonic_clock()
        if remaining <= 0.0:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.AUTOMATIC_ENGAGEMENT,
                speed_kmh=context.actual_speed_kmh,
                post_gear=post_gear,
            )
        try:
            previous_frame = _wait_for_fresh_tick(world, remaining, previous_frame)
            automatic_control = vehicle.get_control()
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.AUTOMATIC_ENGAGEMENT,
                speed_kmh=context.actual_speed_kmh,
                post_gear=post_gear,
            ) from error
        post_gear = int(automatic_control.gear)
        if (
            post_gear > 0
            and not automatic_control.reverse
            and not automatic_control.manual_gear_shift
        ):
            return post_gear
        if post_gear < 0 or automatic_control.reverse:
            raise TransmissionPreparationError(
                TransmissionPreparationFailure.AUTOMATIC_ENGAGEMENT,
                speed_kmh=context.actual_speed_kmh,
                post_gear=post_gear,
            )
