from dataclasses import replace
from math import isfinite
from random import Random

import pytest

from src.p8.ffb_model import ZERO, FfbParameters, calculate_parameters
from src.p8.ffb_safety import FfbLimits, FfbRequest, FfbSafety, FfbState
from tests.test_ffb_model import observation

ENABLE = FfbRequest.ENABLE_EVALUATION


def step(safety, now, *, request=FfbRequest.HOLD, target=None, **changes):
    sample = observation(now, **changes)
    return safety.step(
        sample, target or calculate_parameters(sample), now=now, request=request
    )


def assert_zero(command):
    assert command.parameters == ZERO
    assert command.physical_output_allowed is False
    assert command.simulation_allowed is False


def test_default_disabled_and_only_explicit_evaluation_can_start_at_zero():
    safety = FfbSafety()
    first = step(safety, 1)
    assert_zero(first)
    assert first.reason == "DEFAULT_DISABLED"
    armed = step(safety, 1.1, request=ENABLE)
    assert armed.state is FfbState.EVALUATING and armed.parameters == ZERO
    assert armed.physical_output_allowed is False
    active = step(safety, 1.2)
    assert active.parameters.spring == pytest.approx(0.01)
    assert active.parameters.damper == pytest.approx(0.01)
    assert active.valid_until <= 1.2 + safety.limits.wheel_max_age_s


def test_strength_caps_and_time_based_slew_hold_for_irregular_frames():
    safety = FfbSafety(FfbLimits(max_spring=0.08, max_damper=0.035))
    target = FfbParameters(1.0, 1.0)
    previous = step(safety, 0, request=ENABLE, target=target)
    now = 0.0
    for dt in (0.02, 0.07, 0.15, 0.01, 0.2) * 8:
        now += dt
        current = step(safety, now, target=target)
        for name, cap in (("spring", 0.08), ("damper", 0.035)):
            value = getattr(current.parameters, name)
            assert 0 <= value <= cap
            assert value - getattr(previous.parameters, name) <= 0.1 * dt + 1e-12
        previous = current
    assert current.parameters == FfbParameters(0.08, 0.035)
    lowered = step(safety, now + 0.1, target=ZERO)
    assert lowered.parameters.spring == pytest.approx(0.06)


def test_user_disable_has_zero_command_and_separate_non_authorizing_decay_preview():
    safety = FfbSafety()
    step(safety, 0, request=ENABLE)
    for i in range(1, 6):
        active = step(safety, i / 10)
    disabled = step(safety, 0.6, request=FfbRequest.DISABLE)
    assert_zero(disabled)
    assert disabled.state is FfbState.DISABLED and disabled.reason == "USER_DISABLED"
    assert disabled.disable_target == ZERO
    assert disabled.decay_preview.spring == pytest.approx(
        active.parameters.spring - 0.02
    )
    assert step(safety, 0.8).decay_preview == ZERO
    assert_zero(step(safety, 0.9))


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"wheel_position": float("nan")}, "INVALID_WHEEL"),
        ({"wheel_position": 2.0}, "INVALID_WHEEL"),
        ({"speed_kmh": -1.0}, "INVALID_SPEED"),
        ({"speed_kmh": float("inf")}, "INVALID_SPEED"),
        ({"speed_kmh": "60"}, "INVALID_SPEED"),
        ({"wheel_observed_at": None}, "WHEEL_TIMESTAMP_INVALID"),
        ({"wheel_observed_at": float("nan")}, "WHEEL_TIMESTAMP_INVALID"),
        ({"vehicle_observed_at": -1}, "VEHICLE_TIMESTAMP_INVALID"),
        ({"wheel_observed_at": 2.1}, "WHEEL_TIMESTAMP_FUTURE"),
        ({"vehicle_observed_at": 2.1}, "VEHICLE_TIMESTAMP_FUTURE"),
        ({"wheel_observed_at": 1.0}, "WHEEL_STALE"),
        ({"vehicle_observed_at": 1.0}, "VEHICLE_STALE"),
        ({"connected": False}, "DEVICE_DISCONNECTED"),
        ({"connected": 1}, "INVALID_STATUS"),
        ({"mode": "UNKNOWN"}, "INVALID_MODE"),
    ],
)
def test_faults_are_latched_and_valid_data_or_enable_does_not_clear_them(
    changes, reason
):
    safety = FfbSafety()
    command = safety.step(
        observation(2, **changes), FfbParameters(0.05, 0.05), now=2, request=ENABLE
    )
    assert_zero(command)
    assert command.state is FfbState.FAULT and command.reason == reason
    assert_zero(step(safety, 2.05, request=ENABLE))
    assert safety.reason == reason
    ack = step(safety, 2.1, request=FfbRequest.ACKNOWLEDGE_FAULT)
    assert_zero(ack)
    assert ack.reason == "FAULT_ACKNOWLEDGED"
    assert_zero(step(safety, 2.15))
    rearm = step(safety, 2.2, request=ENABLE)
    assert rearm.simulation_allowed and rearm.parameters == ZERO


@pytest.mark.parametrize("now", [-1, float("nan"), float("inf"), None, True])
def test_bad_clock_returns_finite_zero_fault_command(now):
    command = FfbSafety().step(observation(), ZERO, now=now, request=ENABLE)
    assert_zero(command)
    assert command.state is FfbState.FAULT
    assert isfinite(command.issued_at) and isfinite(command.valid_until)


def test_clock_regression_and_update_gap_cannot_restore_old_strength():
    for now, reason in ((0.9, "CLOCK_REGRESSION"), (1.4, "UPDATE_GAP")):
        safety = FfbSafety()
        step(safety, 1, request=ENABLE)
        command = step(safety, now)
        assert_zero(command)
        assert command.reason == reason


def test_original_timestamp_is_not_replaced_by_update_time_and_regression_is_detected():
    safety = FfbSafety()
    step(safety, 1, request=ENABLE)
    fresh = observation(1)
    assert safety.step(fresh, ZERO, now=1.1).simulation_allowed
    command = safety.step(fresh, ZERO, now=1.16)
    assert command.reason == "WHEEL_STALE"
    other = FfbSafety()
    step(other, 1, request=ENABLE)
    assert step(other, 1.05, wheel_observed_at=0.99).reason == "OBSERVATION_REGRESSION"


@pytest.mark.parametrize(
    "field", ["session_id", "stage_id", "actor_id", "device_generation"]
)
def test_context_change_invalidates_previous_evaluation_even_with_enable_request(field):
    safety = FfbSafety()
    step(safety, 1, request=ENABLE)
    step(safety, 1.1)
    context = replace(observation().context, **{field: "new-identity"})
    changed = step(safety, 1.2, context=context, request=ENABLE)
    assert_zero(changed)
    assert changed.reason == "CONTEXT_CHANGED" and changed.decay_preview == ZERO
    assert_zero(step(safety, 1.3, context=context))
    assert step(safety, 1.4, context=context, request=ENABLE).parameters == ZERO


def test_noa_policy_and_both_mode_transition_directions_invalidate_evaluation():
    safety = FfbSafety()
    step(safety, 1, request=ENABLE)
    assert_zero(step(safety, 1.1, mode="NOA_ACTIVE", request=ENABLE))
    blocked = step(safety, 1.2, mode="NOA_ACTIVE", request=ENABLE)
    assert blocked.reason == "NOA_POLICY_UNAPPROVED"
    assert_zero(blocked)
    assert_zero(step(safety, 1.3, request=ENABLE))
    assert_zero(step(safety, 1.4))


def test_disconnect_reconnect_needs_acknowledgement_and_fresh_enable():
    safety = FfbSafety()
    step(safety, 1, request=ENABLE)
    step(safety, 1.1)
    assert_zero(step(safety, 1.2, connected=False))
    context = replace(observation().context, device_generation="reconnected")
    assert step(safety, 1.3, context=context, request=ENABLE).state is FfbState.FAULT
    assert_zero(
        step(safety, 1.4, context=context, request=FfbRequest.ACKNOWLEDGE_FAULT)
    )
    assert_zero(step(safety, 1.5, context=context))
    assert step(safety, 1.6, context=context, request=ENABLE).parameters == ZERO


def test_missing_inputs_inactive_session_and_closed_state_cannot_auto_resume():
    safety = FfbSafety()
    step(safety, 1, request=ENABLE)
    assert_zero(safety.step(None, None, now=1.1))
    assert_zero(step(safety, 1.2))
    assert_zero(step(safety, 1.3, request=ENABLE, session_active=False))
    assert_zero(step(safety, 1.4))
    assert_zero(safety.close(now=1.5))
    assert step(safety, 1.6, request=ENABLE).state is FfbState.CLOSED


def test_inactive_stage_does_not_inherit_session_permission():
    safety = FfbSafety()
    step(safety, 1, request=ENABLE)
    inactive = step(safety, 1.1, stage_active=False)
    assert_zero(inactive)
    assert inactive.reason == "STAGE_INACTIVE"
    assert_zero(step(safety, 1.2))


@pytest.mark.parametrize(
    "target",
    [
        FfbParameters(-0.1, 0),
        FfbParameters(0, 2),
        FfbParameters(float("nan"), 0),
        FfbParameters(0.1, 0.1, 0.2),
    ],
)
def test_independent_target_validation_including_no_moving_center(target):
    command = FfbSafety().step(observation(), target, now=1, request=ENABLE)
    assert_zero(command)
    assert command.reason == "INVALID_TARGET"


def test_seeded_long_sequence_keeps_all_emitted_values_finite_bounded_and_nonphysical():
    random = Random(42)
    safety = FfbSafety()
    now = 0.0
    for i in range(1000):
        now += random.uniform(0.01, 0.1)
        request = random.choice(tuple(FfbRequest))
        command = step(
            safety,
            now,
            request=request,
            speed_kmh=random.uniform(0, 160),
            wheel_position=random.uniform(-1, 1),
            connected=i % 57 != 0,
        )
        assert command.physical_output_allowed is False
        for values in (
            command.parameters,
            command.decay_preview,
            command.disable_target,
        ):
            assert 0 <= values.spring <= safety.limits.max_spring
            assert 0 <= values.damper <= safety.limits.max_damper
            assert values.center == 0
        if command.state is not FfbState.EVALUATING:
            assert_zero(command)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_spring": float("nan")},
        {"max_damper": 2},
        {"rise_per_second": 0},
        {"wheel_max_age_s": -1},
    ],
)
def test_invalid_safety_configuration_cannot_be_constructed(changes):
    with pytest.raises(ValueError):
        FfbLimits(**changes)
