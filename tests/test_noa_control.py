from __future__ import annotations

from math import inf, nan

import pytest

from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import (
    LongitudinalControlConfig,
    LongitudinalControlValidationError,
)
from src.experiment.noa_control import (
    NoAControlCommand,
    NoAControlValidationError,
    compute_noa_control,
)


def make_longitudinal_config() -> LongitudinalControlConfig:
    return LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
    )


def make_lateral_config() -> LateralControlConfig:
    return LateralControlConfig(
        lateral_error_gain=0.2,
        heading_error_gain=0.5,
        lateral_deadband_m=0.1,
        heading_deadband_rad=0.05,
        max_steering=0.8,
    )


def compute(
    current_speed_kmh: float,
    *,
    lateral_error_m: float = 0.0,
    heading_error_rad: float = 0.0,
) -> NoAControlCommand:
    return compute_noa_control(
        current_speed_kmh,
        LaneGeometryObservation(lateral_error_m, heading_error_rad),
        make_longitudinal_config(),
        make_lateral_config(),
    )


def test_below_target_and_centered_lane_combines_throttle_with_neutral_steering() -> (
    None
):
    command = compute(95.0)

    assert command.throttle == pytest.approx(0.5)
    assert command.brake == 0.0
    assert command.steering == 0.0


def test_above_target_and_centered_lane_combines_brake_with_neutral_steering() -> None:
    command = compute(105.0)

    assert command.throttle == 0.0
    assert command.brake == pytest.approx(0.5)
    assert command.steering == 0.0


def test_target_speed_deadband_combines_neutral_longitudinal_command() -> None:
    assert compute(98.0) == NoAControlCommand(0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("lateral_error_m", "expected_sign"),
    [(0.5, -1.0), (-0.5, 1.0)],
)
def test_lateral_error_commands_opposite_sign_correction(
    lateral_error_m: float,
    expected_sign: float,
) -> None:
    command = compute(100.0, lateral_error_m=lateral_error_m)

    assert command.steering * expected_sign > 0.0


@pytest.mark.parametrize(
    ("heading_error_rad", "expected_sign"),
    [(0.2, -1.0), (-0.2, 1.0)],
)
def test_heading_error_commands_opposite_sign_correction(
    heading_error_rad: float,
    expected_sign: float,
) -> None:
    command = compute(100.0, heading_error_rad=heading_error_rad)

    assert command.steering * expected_sign > 0.0


def test_longitudinal_and_lateral_outputs_are_combined_without_recalculation() -> None:
    command = compute(95.0, lateral_error_m=0.5, heading_error_rad=0.2)

    assert command == NoAControlCommand(throttle=0.5, brake=0.0, steering=-0.2)


def test_existing_lower_level_clamps_are_preserved() -> None:
    command = compute(200.0, lateral_error_m=10.0, heading_error_rad=1.0)

    assert command == NoAControlCommand(throttle=0.0, brake=0.7, steering=-0.8)


@pytest.mark.parametrize("current_speed_kmh", [0.0, 95.0, 100.0, 105.0, 200.0])
def test_throttle_and_brake_remain_mutually_exclusive(
    current_speed_kmh: float,
) -> None:
    command = compute(current_speed_kmh, lateral_error_m=0.5)

    assert not (command.throttle > 0.0 and command.brake > 0.0)


def test_repeated_same_inputs_return_the_same_command() -> None:
    first = compute(95.0, lateral_error_m=0.5, heading_error_rad=-0.2)
    second = compute(95.0, lateral_error_m=0.5, heading_error_rad=-0.2)

    assert first == second


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ((-0.1, 0.0, 0.0), "throttle"),
        ((1.1, 0.0, 0.0), "throttle"),
        ((0.0, -0.1, 0.0), "brake"),
        ((0.0, 1.1, 0.0), "brake"),
        ((0.0, 0.0, -1.1), "steering"),
        ((0.0, 0.0, 1.1), "steering"),
        ((nan, 0.0, 0.0), "throttle"),
        ((0.0, inf, 0.0), "brake"),
        ((0.0, 0.0, nan), "steering"),
    ],
)
def test_combined_command_rejects_invalid_normalized_values(
    values: tuple[float, float, float],
    field: str,
) -> None:
    with pytest.raises(NoAControlValidationError) as caught:
        NoAControlCommand(*values)

    assert caught.value.field == field


def test_combined_command_rejects_simultaneous_throttle_and_brake() -> None:
    with pytest.raises(NoAControlValidationError) as caught:
        NoAControlCommand(throttle=0.1, brake=0.1, steering=0.0)

    assert caught.value.field == "throttle_and_brake"


@pytest.mark.parametrize("current_speed_kmh", [-0.1, nan, inf, -inf])
def test_invalid_current_speed_propagates_longitudinal_validation(
    current_speed_kmh: float,
) -> None:
    with pytest.raises(LongitudinalControlValidationError) as caught:
        compute(current_speed_kmh)

    assert caught.value.field == "current_speed_kmh"
