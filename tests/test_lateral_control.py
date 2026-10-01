from __future__ import annotations

from math import inf, nan, pi

import pytest

from src.experiment.lateral_control import (
    LateralControlCommand,
    LateralControlConfig,
    LateralControlValidationError,
    compute_lateral_control,
)


def make_config() -> LateralControlConfig:
    return LateralControlConfig(
        lateral_error_gain=0.2,
        heading_error_gain=0.5,
        lateral_deadband_m=0.1,
        heading_deadband_rad=0.05,
        max_steering=0.8,
    )


def test_centered_and_aligned_commands_neutral_steering() -> None:
    command = compute_lateral_control(0.0, 0.0, make_config())

    assert command.steering == 0.0


def test_positive_lateral_error_commands_left_correction() -> None:
    command = compute_lateral_control(0.5, 0.0, make_config())

    assert command.steering == pytest.approx(-0.1)


def test_negative_lateral_error_commands_right_correction() -> None:
    command = compute_lateral_control(-0.5, 0.0, make_config())

    assert command.steering == pytest.approx(0.1)


def test_positive_heading_error_commands_left_correction() -> None:
    command = compute_lateral_control(0.0, 0.2, make_config())

    assert command.steering == pytest.approx(-0.1)


def test_negative_heading_error_commands_right_correction() -> None:
    command = compute_lateral_control(0.0, -0.2, make_config())

    assert command.steering == pytest.approx(0.1)


def test_combined_errors_sum_both_proportional_components() -> None:
    command = compute_lateral_control(0.5, 0.2, make_config())

    assert command.steering == pytest.approx(-0.2)


@pytest.mark.parametrize("lateral_error_m", [0.1, -0.1])
def test_lateral_deadband_boundaries_remove_lateral_component(
    lateral_error_m: float,
) -> None:
    command = compute_lateral_control(lateral_error_m, 0.0, make_config())

    assert command.steering == 0.0


@pytest.mark.parametrize("heading_error_rad", [0.05, -0.05])
def test_heading_deadband_boundaries_remove_heading_component(
    heading_error_rad: float,
) -> None:
    command = compute_lateral_control(0.0, heading_error_rad, make_config())

    assert command.steering == 0.0


@pytest.mark.parametrize(
    ("lateral_error_m", "heading_error_rad", "expected_steering"),
    [(0.1, 0.2, -0.1), (0.5, 0.05, -0.1)],
)
def test_only_component_outside_its_deadband_contributes(
    lateral_error_m: float,
    heading_error_rad: float,
    expected_steering: float,
) -> None:
    command = compute_lateral_control(lateral_error_m, heading_error_rad, make_config())

    assert command.steering == pytest.approx(expected_steering)


def test_large_negative_errors_clamp_positive_steering() -> None:
    command = compute_lateral_control(-10.0, -1.0, make_config())

    assert command.steering == 0.8


def test_large_positive_errors_clamp_negative_steering() -> None:
    command = compute_lateral_control(10.0, 1.0, make_config())

    assert command.steering == -0.8


@pytest.mark.parametrize(
    ("lateral_error_m", "heading_error_rad"),
    [(-100.0, -pi), (-1.0, 0.0), (0.0, 0.0), (1.0, 0.0), (100.0, pi)],
)
def test_steering_is_always_within_configured_limit(
    lateral_error_m: float,
    heading_error_rad: float,
) -> None:
    command = compute_lateral_control(lateral_error_m, heading_error_rad, make_config())

    assert -0.8 <= command.steering <= 0.8


def test_repeated_computation_is_deterministic() -> None:
    config = make_config()

    first = compute_lateral_control(0.5, -0.2, config)
    second = compute_lateral_control(0.5, -0.2, config)

    assert first == second


@pytest.mark.parametrize("lateral_error_m", [nan, inf, -inf])
def test_nonfinite_lateral_error_is_rejected(lateral_error_m: float) -> None:
    with pytest.raises(LateralControlValidationError) as caught:
        compute_lateral_control(lateral_error_m, 0.0, make_config())

    assert caught.value.field == "lateral_error_m"


@pytest.mark.parametrize("heading_error_rad", [nan, inf, -inf])
def test_nonfinite_heading_error_is_rejected(heading_error_rad: float) -> None:
    with pytest.raises(LateralControlValidationError) as caught:
        compute_lateral_control(0.0, heading_error_rad, make_config())

    assert caught.value.field == "heading_error_rad"


@pytest.mark.parametrize("heading_error_rad", [pi + 0.001, -pi - 0.001])
def test_heading_error_outside_signed_minimal_range_is_rejected(
    heading_error_rad: float,
) -> None:
    with pytest.raises(LateralControlValidationError) as caught:
        compute_lateral_control(0.0, heading_error_rad, make_config())

    assert caught.value.field == "heading_error_rad"


@pytest.mark.parametrize(
    "values",
    [
        (0.0, 0.5, 0.1, 0.05, 0.8),
        (-0.1, 0.5, 0.1, 0.05, 0.8),
        (nan, 0.5, 0.1, 0.05, 0.8),
        (inf, 0.5, 0.1, 0.05, 0.8),
        (0.2, 0.0, 0.1, 0.05, 0.8),
        (0.2, -0.1, 0.1, 0.05, 0.8),
        (0.2, nan, 0.1, 0.05, 0.8),
        (0.2, inf, 0.1, 0.05, 0.8),
        (0.2, 0.5, -0.1, 0.05, 0.8),
        (0.2, 0.5, nan, 0.05, 0.8),
        (0.2, 0.5, inf, 0.05, 0.8),
        (0.2, 0.5, 0.1, -0.01, 0.8),
        (0.2, 0.5, 0.1, nan, 0.8),
        (0.2, 0.5, 0.1, inf, 0.8),
        (0.2, 0.5, 0.1, 0.05, 0.0),
        (0.2, 0.5, 0.1, 0.05, -0.1),
        (0.2, 0.5, 0.1, 0.05, 1.1),
        (0.2, 0.5, 0.1, 0.05, nan),
        (0.2, 0.5, 0.1, 0.05, inf),
    ],
)
def test_invalid_config_is_rejected(
    values: tuple[float, float, float, float, float],
) -> None:
    with pytest.raises(LateralControlValidationError):
        LateralControlConfig(*values)


@pytest.mark.parametrize("steering", [-1.1, 1.1, nan, inf, -inf])
def test_invalid_command_is_rejected(steering: float) -> None:
    with pytest.raises(LateralControlValidationError):
        LateralControlCommand(steering)
