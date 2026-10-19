from dataclasses import fields, replace
from math import isfinite

import pytest

from src.scenario.vehicle_audio_model import EngineSoundModel, VehicleAudioState


def state(now=0.0, **kwargs):
    return replace(
        VehicleAudioState(0.0, 0.0, 0.0, 1, False, now, "hero", "training", "MANUAL"),
        **kwargs,
    )


def settle(**kwargs):
    model = EngineSoundModel()
    for i in range(121):
        result = model.update(state(i / 60, **kwargs), i / 60)
    return result


def test_idle_has_estimated_running_engine_and_no_road_or_wind():
    p = settle()
    assert p.rpm == pytest.approx(750)
    assert p.rpm_source == "estimated_rpm"
    assert 0 < p.engine_volume < 0.2
    assert p.idle_blend == 1 and p.engine_load == 0
    assert p.road_gain == p.wind_gain == 0


def test_same_speed_acceleration_and_cruise_have_different_load_timbre_and_gain():
    cruise = settle(speed_kmh=60, gear=4, throttle=0.15)
    accel = settle(speed_kmh=60, gear=4, throttle=0.9)
    assert accel.engine_load > cruise.engine_load + 0.5
    assert accel.engine_volume > cruise.engine_volume
    assert accel.acceleration_blend > cruise.acceleration_blend
    assert accel.road_gain == cruise.road_gain
    assert accel.rpm == cruise.rpm  # locked acoustic gear, load is independent of pitch


def test_speed_gear_and_brake_are_not_a_single_volume_curve():
    low = settle(speed_kmh=30, gear=3, throttle=0.6)
    high = settle(speed_kmh=60, gear=3, throttle=0.6)
    brake = settle(speed_kmh=60, gear=3, throttle=0, brake=1)
    assert high.rpm > low.rpm
    assert high.road_gain > low.road_gain and high.wind_gain > low.wind_gain
    assert brake.rpm == high.rpm and brake.rpm > 750
    assert brake.engine_load < high.engine_load
    assert brake.deceleration_blend > high.deceleration_blend


def test_gear_shift_is_smoothed_and_not_silenced():
    model = EngineSoundModel()
    for i in range(121):
        before = model.update(state(i / 60, speed_kmh=60, gear=2, throttle=0.6), i / 60)
    after = model.update(state(2.02, speed_kmh=60, gear=3, throttle=0.6), 2.02)
    assert before.rpm > after.rpm
    assert before.rpm - after.rpm <= 5000 * 0.02 + 1e-8
    assert after.engine_volume > 0 and after.gear_shift_transition == 1


def test_neutral_rev_and_reverse_are_not_forward_gear_indexing():
    idle = settle(gear=0)
    neutral = settle(gear=0, throttle=0.8)
    reverse = settle(gear=-1, reverse=True, speed_kmh=20, throttle=0.3)
    assert neutral.rpm > idle.rpm + 2000
    assert 750 < reverse.rpm < 6500


@pytest.mark.parametrize("gear", [None, 99, -9, float("nan"), "missing"])
def test_missing_or_invalid_gear_uses_bounded_explicit_estimate(gear):
    result = settle(gear=gear, speed_kmh=80, throttle=0.4)
    assert 750 <= result.rpm <= 6500
    assert result.rpm_source == "estimated_rpm"


@pytest.mark.parametrize(
    "field,value",
    [
        ("speed_kmh", -1),
        ("speed_kmh", float("nan")),
        ("throttle", float("inf")),
        ("brake", float("nan")),
        ("observed_at", float("inf")),
    ],
)
def test_invalid_sensor_values_are_finite_and_not_fresh(field, value):
    result = EngineSoundModel().update(state(**{field: value}), 0)
    assert not result.fresh
    assert all(
        isfinite(getattr(result, f.name))
        for f in fields(result)
        if isinstance(getattr(result, f.name), float)
    )


def test_old_future_missing_and_nonfinite_time_do_not_reauthorize_playback():
    model = EngineSoundModel()
    assert not model.update(state(), 1).fresh
    assert not model.update(state(5), 1).fresh
    assert not model.update(None, 2).fresh
    assert not model.update(state(), float("nan")).fresh


def test_optional_measured_rpm_is_distinguished_and_bad_rpm_falls_back():
    assert settle(measured_rpm=3000).rpm_source == "measured_rpm"
    assert settle(measured_rpm=3000).rpm == pytest.approx(3000, abs=1)
    assert settle(measured_rpm=float("nan")).rpm_source == "estimated_rpm"


def test_mode_change_alone_is_bit_identical_and_stage_change_resets_envelope():
    a, b = EngineSoundModel(), EngineSoundModel()
    for i in range(200):
        now = i / 50
        s = state(now, speed_kmh=70, throttle=0.4, gear=4)
        assert a.update(s, now) == b.update(
            replace(s, mode="NOA_ACTIVE" if i > 80 else "MANUAL"), now
        )
    assert a.update(replace(s, stage_id="M1"), now).engine_volume == 0


def test_real_time_smoothing_is_frame_rate_independent_for_steady_state():
    results = []
    for dt in (1 / 60, 0.1, 0.25):
        model = EngineSoundModel()
        for i in range(round(3 / dt) + 1):
            result = model.update(
                state(i * dt, speed_kmh=60, gear=4, throttle=0.5), i * dt
            )
        results.append(result)
    for result in results[1:]:
        assert result.engine_load == pytest.approx(results[0].engine_load, abs=1e-6)
        assert result.rpm == pytest.approx(results[0].rpm, abs=0.1)


def test_control_range_clamping_and_stopped_engine():
    result = settle(speed_kmh=999, gear=1, throttle=5, brake=-1)
    assert result.rpm <= 6500 and 0 <= result.engine_load <= 1
    assert result.engine_pitch <= 6500 / 1500
    assert settle(engine_running=False).engine_volume == 0
