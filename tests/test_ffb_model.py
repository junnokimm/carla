from dataclasses import replace
from math import isfinite

import pytest

from src.p8.ffb_model import FfbContext, FfbCurve, FfbObservation, calculate_parameters


def observation(now=1.0, **changes):
    return replace(
        FfbObservation(
            FfbContext("session", "training", "hero", "connection-1"),
            0.0,
            now,
            0.0,
            now,
        ),
        **changes,
    )


def test_rest_and_speed_curve_are_distinct_condition_coefficients_not_torque():
    rest = calculate_parameters(observation())
    fast = calculate_parameters(observation(speed_kmh=100))
    assert rest.spring == 0.06 and rest.damper == 0.025
    assert fast.spring < rest.spring and fast.damper > rest.damper
    assert rest.center == fast.center == 0
    assert calculate_parameters(observation(speed_kmh=1000)) == fast


def test_curve_can_reverse_speed_trends_without_claiming_a_vehicle_calibration():
    curve = FfbCurve(
        spring_at_rest=0.01,
        spring_at_reference=0.04,
        damper_at_rest=0.08,
        damper_at_reference=0.02,
    )
    slow = calculate_parameters(observation(), curve)
    mid = calculate_parameters(observation(speed_kmh=50), curve)
    fast = calculate_parameters(observation(speed_kmh=100), curve)
    assert slow.spring < mid.spring < fast.spring
    assert slow.damper > mid.damper > fast.damper
    assert mid.spring == pytest.approx((slow.spring + fast.spring) / 2)


@pytest.mark.parametrize("position", [-1.0, -0.3, 0.0, 0.3, 1.0])
def test_wheel_displacement_is_not_mistaken_for_damper_torque(position):
    params = calculate_parameters(observation(wheel_position=position))
    assert params == calculate_parameters(observation())
    assert all(isfinite(v) and 0 <= v <= 1 for v in (params.spring, params.damper))


@pytest.mark.parametrize(
    "field,bad",
    [
        ("wheel_position", -1.01),
        ("wheel_position", 1.01),
        ("wheel_position", float("nan")),
        ("wheel_position", None),
        ("wheel_position", True),
        ("speed_kmh", -1),
        ("speed_kmh", float("inf")),
        ("speed_kmh", float("nan")),
        ("speed_kmh", "60"),
        ("speed_kmh", 10**500),
    ],
)
def test_invalid_numeric_inputs_are_rejected(field, bad):
    with pytest.raises(ValueError):
        calculate_parameters(observation(**{field: bad}))


@pytest.mark.parametrize(
    "changes",
    [
        {"reference_speed_kmh": 0},
        {"reference_speed_kmh": float("nan")},
        {"spring_at_rest": -0.1},
        {"damper_at_reference": 1.1},
    ],
)
def test_invalid_curve_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        FfbCurve(**changes)
