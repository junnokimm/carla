from __future__ import annotations

import pytest

from src.experiment.longitudinal_control import (
    LongitudinalControlCommand,
    LongitudinalControlConfig,
    LongitudinalController,
    compute_longitudinal_control,
)


def _config(*, integral_gain: float = 0.02) -> LongitudinalControlConfig:
    return LongitudinalControlConfig(
        target_speed_kmh=10.0,
        speed_deadband_kmh=1.0,
        acceleration_gain=0.1,
        braking_gain=0.1,
        max_throttle=0.4,
        max_brake=0.5,
        integral_gain=integral_gain,
    )


def _controller_with_integral(training_speed_kmh: float) -> LongitudinalController:
    controller = LongitudinalController(_config())
    for step in range(301):
        controller.compute(training_speed_kmh, control_time_seconds=step * 0.05)
    return controller


def test_positive_integral_is_continuous_across_upper_deadband_boundary() -> None:
    # Given
    inside_controller = _controller_with_integral(9.0)
    boundary_controller = _controller_with_integral(9.0)
    outside_controller = _controller_with_integral(9.0)

    # When
    inside = inside_controller.compute(10.999, control_time_seconds=15.0)
    boundary = boundary_controller.compute(11.0, control_time_seconds=15.0)
    outside = outside_controller.compute(11.001, control_time_seconds=15.0)

    # Then
    assert inside.throttle == pytest.approx(0.3)
    assert inside.brake == 0.0
    assert boundary.throttle == pytest.approx(0.3)
    assert boundary.brake == 0.0
    assert outside.throttle == pytest.approx(0.2999)
    assert outside.brake == 0.0
    assert boundary.throttle - outside.throttle == pytest.approx(0.0001)


def test_negative_integral_is_continuous_across_lower_deadband_boundary() -> None:
    # Given
    inside_controller = _controller_with_integral(11.0)
    boundary_controller = _controller_with_integral(11.0)
    outside_controller = _controller_with_integral(11.0)

    # When
    inside = inside_controller.compute(9.001, control_time_seconds=15.0)
    boundary = boundary_controller.compute(9.0, control_time_seconds=15.0)
    outside = outside_controller.compute(8.999, control_time_seconds=15.0)

    # Then
    assert inside.throttle == 0.0
    assert inside.brake == pytest.approx(0.3)
    assert boundary.throttle == 0.0
    assert boundary.brake == pytest.approx(0.3)
    assert outside.throttle == 0.0
    assert outside.brake == pytest.approx(0.2999)
    assert boundary.brake - outside.brake == pytest.approx(0.0001)


@pytest.mark.parametrize(
    ("training_speed", "stored_effort"), [(9.0, 0.3), (11.0, -0.3)]
)
@pytest.mark.parametrize(
    ("speed", "proportional_effort"),
    [
        (8.999, 0.0001),
        (9.0, 0.0),
        (9.001, 0.0),
        (10.999, 0.0),
        (11.0, 0.0),
        (11.001, -0.0001),
    ],
)
def test_both_integral_signs_use_full_sum_at_both_boundaries(
    training_speed: float,
    stored_effort: float,
    speed: float,
    proportional_effort: float,
) -> None:
    controller = _controller_with_integral(training_speed)

    command = controller.compute(speed, control_time_seconds=15.0)

    assert controller.integral_effort == pytest.approx(stored_effort)
    assert command.throttle - command.brake == pytest.approx(
        stored_effort + proportional_effort
    )
    assert not (command.throttle > 0.0 and command.brake > 0.0)


def test_large_overspeed_brakes_when_total_effort_becomes_negative() -> None:
    # Given
    controller = _controller_with_integral(9.0)

    # When
    command = controller.compute(20.0, control_time_seconds=15.0)

    # Then
    assert command == LongitudinalControlCommand(0.0, 0.5)


def test_large_underspeed_accelerates_when_total_effort_becomes_positive() -> None:
    # Given
    controller = _controller_with_integral(11.0)

    # When
    command = controller.compute(0.0, control_time_seconds=15.0)

    # Then
    assert command == LongitudinalControlCommand(0.4, 0.0)


@pytest.mark.parametrize("speed_kmh", [0.0, 8.999, 9.0, 10.0, 11.0, 11.001, 20.0])
def test_zero_integral_gain_preserves_stateless_legacy_command(
    speed_kmh: float,
) -> None:
    # Given
    config = _config(integral_gain=0.0)
    controller = LongitudinalController(config)
    controller.compute(speed_kmh, control_time_seconds=0.0)

    # When
    stateful = controller.compute(speed_kmh, control_time_seconds=10.0)

    # Then
    assert stateful == compute_longitudinal_control(speed_kmh, config)


@pytest.mark.parametrize("saturated_speed_kmh", [0.0, 20.0])
def test_continuous_proportional_effort_preserves_saturation_antiwindup(
    saturated_speed_kmh: float,
) -> None:
    # Given
    controller = LongitudinalController(_config())
    controller.compute(saturated_speed_kmh, control_time_seconds=0.0)

    # When
    command = controller.compute(saturated_speed_kmh, control_time_seconds=100.0)

    # Then
    assert controller.integral_effort == 0.0
    assert 0.0 <= command.throttle <= 0.4
    assert 0.0 <= command.brake <= 0.5
    assert not (command.throttle > 0.0 and command.brake > 0.0)
