from __future__ import annotations

from math import inf, nan

import pytest

from src.experiment import longitudinal_control
from src.experiment.longitudinal_control import (
    LongitudinalControlCommand,
    LongitudinalControlConfig,
    LongitudinalControlValidationError,
    compute_longitudinal_control,
)


def make_config() -> LongitudinalControlConfig:
    return LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
    )


def test_below_target_commands_throttle_only() -> None:
    command = compute_longitudinal_control(95.0, make_config())

    assert command.throttle == pytest.approx(0.5)
    assert command.brake == 0.0


def test_above_target_commands_brake_only() -> None:
    command = compute_longitudinal_control(105.0, make_config())

    assert command.throttle == 0.0
    assert command.brake == pytest.approx(0.5)


def test_target_speed_commands_coast() -> None:
    command = compute_longitudinal_control(100.0, make_config())

    assert command == LongitudinalControlCommand(throttle=0.0, brake=0.0)


def test_repeated_below_target_error_inside_deadband_builds_propulsion() -> None:
    config = LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    controller = longitudinal_control.LongitudinalController(config)

    initial = controller.compute(99.0, control_time_seconds=0.0)
    sustained = controller.compute(99.0, control_time_seconds=1.0)

    assert initial == LongitudinalControlCommand(throttle=0.0, brake=0.0)
    assert sustained.throttle > 0.0
    assert sustained.brake == 0.0


def test_integral_effort_sustains_propulsion_at_target_speed() -> None:
    config = LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    controller = longitudinal_control.LongitudinalController(config)
    controller.compute(99.0, control_time_seconds=0.0)
    learned = controller.compute(99.0, control_time_seconds=10.0)

    sustained = controller.compute(100.0, control_time_seconds=10.0)

    assert sustained == learned
    assert sustained.throttle > 0.0


def test_integral_effort_remains_within_actuator_bounds() -> None:
    config = LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    accelerating = longitudinal_control.LongitudinalController(config)
    braking = longitudinal_control.LongitudinalController(config)
    accelerating.compute(99.0, control_time_seconds=0.0)
    braking.compute(101.0, control_time_seconds=0.0)

    throttle = accelerating.compute(99.0, control_time_seconds=100.0)
    brake = braking.compute(101.0, control_time_seconds=100.0)

    assert throttle == LongitudinalControlCommand(config.max_throttle, 0.0)
    assert brake == LongitudinalControlCommand(0.0, config.max_brake)


def test_saturated_proportional_effort_does_not_wind_up_integral_state() -> None:
    config = LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    controller = longitudinal_control.LongitudinalController(config)
    controller.compute(0.0, control_time_seconds=0.0)

    command = controller.compute(0.0, control_time_seconds=100.0)

    assert command == LongitudinalControlCommand(config.max_throttle, 0.0)
    assert controller.integral_effort == 0.0


def test_learned_propulsion_combines_with_braking_outside_deadband() -> None:
    config = LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    controller = longitudinal_control.LongitudinalController(config)
    controller.compute(99.0, control_time_seconds=0.0)
    controller.compute(99.0, control_time_seconds=100.0)

    command = controller.compute(102.1, control_time_seconds=100.0)

    assert command.throttle == pytest.approx(0.59)
    assert command.brake == 0.0


def test_positive_deadband_boundary_commands_coast() -> None:
    command = compute_longitudinal_control(98.0, make_config())

    assert command == LongitudinalControlCommand(throttle=0.0, brake=0.0)


def test_negative_deadband_boundary_commands_coast() -> None:
    command = compute_longitudinal_control(102.0, make_config())

    assert command == LongitudinalControlCommand(throttle=0.0, brake=0.0)


def test_large_positive_error_clamps_maximum_throttle() -> None:
    command = compute_longitudinal_control(0.0, make_config())

    assert command == LongitudinalControlCommand(throttle=0.6, brake=0.0)


def test_large_negative_error_clamps_maximum_brake() -> None:
    command = compute_longitudinal_control(200.0, make_config())

    assert command == LongitudinalControlCommand(throttle=0.0, brake=0.7)


@pytest.mark.parametrize(
    "current_speed_kmh", [0.0, 95.0, 98.0, 100.0, 102.0, 105.0, 200.0]
)
def test_throttle_and_brake_are_never_simultaneously_positive(
    current_speed_kmh: float,
) -> None:
    command = compute_longitudinal_control(current_speed_kmh, make_config())

    assert not (command.throttle > 0.0 and command.brake > 0.0)


def test_repeated_computation_is_deterministic() -> None:
    config = make_config()

    first = compute_longitudinal_control(95.0, config)
    second = compute_longitudinal_control(95.0, config)

    assert first == second


@pytest.mark.parametrize("current_speed_kmh", [-0.1, nan, inf, -inf])
def test_invalid_current_speed_is_rejected(current_speed_kmh: float) -> None:
    with pytest.raises(LongitudinalControlValidationError) as caught:
        compute_longitudinal_control(current_speed_kmh, make_config())

    assert caught.value.field == "current_speed_kmh"


@pytest.mark.parametrize(
    "values",
    [
        (-1.0, 2.0, 0.1, 0.1, 0.6, 0.7),
        (nan, 2.0, 0.1, 0.1, 0.6, 0.7),
        (inf, 2.0, 0.1, 0.1, 0.6, 0.7),
        (100.0, -1.0, 0.1, 0.1, 0.6, 0.7),
        (100.0, nan, 0.1, 0.1, 0.6, 0.7),
        (100.0, inf, 0.1, 0.1, 0.6, 0.7),
        (100.0, 2.0, 0.0, 0.1, 0.6, 0.7),
        (100.0, 2.0, -0.1, 0.1, 0.6, 0.7),
        (100.0, 2.0, nan, 0.1, 0.6, 0.7),
        (100.0, 2.0, inf, 0.1, 0.6, 0.7),
        (100.0, 2.0, 0.1, 0.0, 0.6, 0.7),
        (100.0, 2.0, 0.1, -0.1, 0.6, 0.7),
        (100.0, 2.0, 0.1, nan, 0.6, 0.7),
        (100.0, 2.0, 0.1, inf, 0.6, 0.7),
        (100.0, 2.0, 0.1, 0.1, 0.0, 0.7),
        (100.0, 2.0, 0.1, 0.1, -0.1, 0.7),
        (100.0, 2.0, 0.1, 0.1, 1.1, 0.7),
        (100.0, 2.0, 0.1, 0.1, nan, 0.7),
        (100.0, 2.0, 0.1, 0.1, inf, 0.7),
        (100.0, 2.0, 0.1, 0.1, 0.6, 0.0),
        (100.0, 2.0, 0.1, 0.1, 0.6, -0.1),
        (100.0, 2.0, 0.1, 0.1, 0.6, 1.1),
        (100.0, 2.0, 0.1, 0.1, 0.6, nan),
        (100.0, 2.0, 0.1, 0.1, 0.6, inf),
    ],
)
def test_invalid_config_is_rejected(
    values: tuple[float, float, float, float, float, float],
) -> None:
    with pytest.raises(LongitudinalControlValidationError):
        LongitudinalControlConfig(*values)


@pytest.mark.parametrize("integral_gain", [-0.1, nan, inf, -inf])
def test_invalid_integral_gain_is_rejected(integral_gain: float) -> None:
    with pytest.raises(LongitudinalControlValidationError) as caught:
        LongitudinalControlConfig(
            target_speed_kmh=100.0,
            speed_deadband_kmh=2.0,
            acceleration_gain=0.1,
            braking_gain=0.1,
            max_throttle=0.6,
            max_brake=0.7,
            integral_gain=integral_gain,
        )

    assert caught.value.field == "integral_gain"


def test_decreasing_control_time_is_rejected_without_changing_integral_effort() -> None:
    config = LongitudinalControlConfig(
        target_speed_kmh=100.0,
        speed_deadband_kmh=2.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.6,
        max_brake=0.7,
        integral_gain=0.02,
    )
    controller = longitudinal_control.LongitudinalController(config)
    controller.compute(99.0, control_time_seconds=1.0)

    with pytest.raises(LongitudinalControlValidationError) as caught:
        controller.compute(99.0, control_time_seconds=0.5)

    assert caught.value.field == "control_time_seconds"
    assert controller.integral_effort == 0.0


@pytest.mark.parametrize(
    ("throttle", "brake"),
    [(-0.1, 0.0), (1.1, 0.0), (nan, 0.0), (0.0, inf)],
)
def test_invalid_command_range_is_rejected(throttle: float, brake: float) -> None:
    with pytest.raises(LongitudinalControlValidationError):
        LongitudinalControlCommand(throttle, brake)


def test_command_rejects_simultaneous_throttle_and_brake() -> None:
    with pytest.raises(LongitudinalControlValidationError):
        LongitudinalControlCommand(throttle=0.1, brake=0.1)
