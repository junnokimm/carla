from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, replace
from math import inf, nan

import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import (
    AutomationInteractionController,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
)
from src.experiment.context import ExperimentModule, Module1Condition
from src.p8.operator_cli import _runtime_config, build_parser
from src.p8.tsxw_input import TsxwInput
from src.p8.tsxw_policy import (
    PILOT_MANUAL_STEERING_CENTER_AXIS,
    TsxwConfig,
    TsxwConfigError,
    normalize_steering,
)
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_automation_interaction import FakeGeometry, geometry, make_controller
from tests.test_driver_view import FakeWorld
from tests.test_p8_pilot_launcher import settings
from tests.test_p8_tsxw_device import FakeJoystick, install_devices
from tools.run_pilot import build_operator_args


def pilot_runtime():
    return _runtime_config(build_parser().parse_args(
        build_operator_args(settings(), participant_id="PILOT", run_id="RUN")
    ))


def test_integrated_launcher_separates_manual_and_noa_deadzone():
    config = pilot_runtime().tsxw_config
    assert config.steering_deadzone == 0.02
    assert config.manual_steering_deadzone == 0.005
    assert config.manual_steering_center_axis == -0.012664794921875
    assert config.manual_steering_center_axis == PILOT_MANUAL_STEERING_CENTER_AXIS
    assert config.brake_pressed_axis == -1.0
    assert (config.steering_left_axis, config.steering_center_axis,
            config.steering_right_axis) == (-1.0, 0.0, 1.0)


@pytest.mark.parametrize("deadzone", [-0.001, 1.0, 2.0, nan, inf, -inf])
def test_invalid_manual_deadzone_rejected_at_configuration(deadzone):
    with pytest.raises(TsxwConfigError):
        TsxwConfig(index=0, steering_deadzone=0.02, manual_steering_deadzone=deadzone)


@pytest.mark.parametrize("center", [-1.0, 1.0, -0.999, 0.999, nan, inf, -inf])
def test_invalid_manual_center_rejected_at_configuration(center):
    with pytest.raises(TsxwConfigError):
        TsxwConfig(index=0, steering_deadzone=0.02,
                   manual_steering_deadzone=0.005, manual_steering_center_axis=center)


def test_no_manual_calibration_preserves_legacy_steering():
    config = TsxwConfig(index=0, steering_deadzone=0.02, steering_center_axis=0.1)
    assert config.effective_manual_steering_center_axis == 0.1
    for raw in (-1.0, -0.01, 0.0, 0.08, 0.1, 0.12, 0.5, 1.0):
        assert normalize_steering(raw, config, manual=True) == normalize_steering(raw, config)


@pytest.mark.parametrize("raw", [
    -0.014068603515625, -0.01373291015625,  # Level: min/max.
    -0.01214599609375,  # From left: constant.
    -0.013885498046875, -0.012664794921875,  # From right: min/max.
])
def test_measured_neutral_range_is_zero_without_noa_takeover(raw):
    config = pilot_runtime().tsxw_config
    assert normalize_steering(raw, config, manual=True) == 0.0
    assert normalize_steering(raw, config) == 0.0


def test_manual_deadzone_respects_calibrated_center_and_endpoints():
    config = TsxwConfig(index=0, steering_deadzone=0.02,
                        steering_center_axis=0.1, manual_steering_deadzone=0.005)
    assert normalize_steering(0.1, config, manual=True) == 0.0
    assert normalize_steering(0.11, config, manual=True) > 0.0
    assert normalize_steering(0.09, config, manual=True) < 0.0
    assert normalize_steering(-1.0, config, manual=True) == -1.0
    assert normalize_steering(1.0, config, manual=True) == 1.0
    with pytest.raises(TsxwConfigError, match="manual steering deadzone"):
        replace(config, manual_steering_deadzone=0.9)


def test_manual_curve_symmetric_continuous_and_preserves_override_and_pedals(monkeypatch):
    device = FakeJoystick(0)
    install_devices(monkeypatch, [device])
    records = []
    config = pilot_runtime().tsxw_config
    changed = TsxwInput.open(config, event_sink=records.append)
    legacy = TsxwInput.open(replace(
        config, manual_steering_deadzone=None, manual_steering_center_axis=None
    ))
    previous = None
    # Sweep the entire axis, including center and both deadzone boundaries.
    for i in range(-1000, 1001):
        raw = i / 1000
        device.axes[:] = [raw, raw, -raw, 0.0]
        old, new = legacy.poll(), changed.poll()
        if previous is not None:
            assert new.steering >= previous
            assert new.steering - previous <= 0.00101
        previous = new.steering
        assert new.steering_engaged == (abs(raw) > 0.02)
        # Every other axis, button edge, and NoA override flag stays identical.
        assert asdict(new) | {"steering": old.steering} == asdict(old)
    center = config.effective_manual_steering_center_axis
    for delta in (-0.005, 0.0, 0.005):
        assert normalize_steering(center + delta, config, manual=True) == 0.0
    for delta in (-0.006, 0.006):
        raw = center + delta
        assert normalize_steering(raw, config) == 0.0
        assert abs(normalize_steering(raw, config, manual=True)) > 0.0
    # Equal movement either side of physical neutral has identical gain.
    for i in range(981):
        delta = i / 1000
        assert normalize_steering(center + delta, config, manual=True) == pytest.approx(
            -normalize_steering(center - delta, config, manual=True), abs=1e-14
        )
    # Crossing either deadzone edge produces a small, continuous response.
    for sign in (-1, 1):
        assert normalize_steering(
            center + sign * 0.005001, config, manual=True
        ) == pytest.approx(sign * 0.000001 / 0.995)
    opened = json.loads(records[0].payload_json)
    assert opened["steering_deadzone"] == 0.02
    assert opened["manual_steering_deadzone"] == 0.005
    assert opened["manual_steering_center_axis"] == center
    assert opened["steering_center_axis"] == 0.0
    changed.close()
    legacy.close()


def make_live_view(monkeypatch, controller):
    device = FakeJoystick(0)
    install_devices(monkeypatch, [device])
    keys = defaultdict(bool)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    monkeypatch.setattr(pygame.key, "get_focused", lambda: True)
    records = []
    vehicle = FakeResearchVehicle()
    view = ResearchLiveDriverView(FakeWorld(), vehicle, ResearchDriverViewConfig(
        tsxw_config=pilot_runtime().tsxw_config,
        tsxw_event_sink=records.append,
        manual_control_limits=ResearchManualControlLimits(1.0, 0.5, 0.15),
        automation_state_source=lambda: controller.state,
    ))
    view._initialize_input()
    return view, device, vehicle, keys, records


def pilot_controller():
    base, backend, events = make_controller()
    controller = AutomationInteractionController(
        runtime=base._runtime, geometry=FakeGeometry(geometry()),
        vehicle=base._vehicle, steering_target=backend,
        config=replace(base._config, steering_deactivation_enabled=True),
        event_sink=events.append,
    )
    controller.initialize(ExperimentModule.MODULE_1, Module1Condition.NO_SURT)
    return controller, events


@pytest.mark.parametrize("sign", [-1, 1])
def test_small_manual_input_and_same_frame_noa_takeover_keep_separate_thresholds(
    monkeypatch, sign
):
    controller, events = pilot_controller()
    view, device, vehicle, keys, records = make_live_view(monkeypatch, controller)

    def frame(raw, keyboard_events=()):
        device.axes[0] = raw
        driver = view._collect_driver_input(list(keyboard_events))
        view._pending_driver_input = driver
        controller.update(driver)
        view._apply_manual_control()
        return driver

    small = frame(PILOT_MANUAL_STEERING_CENTER_AXIS + sign * 0.006)
    assert small.steering_engaged is False
    assert vehicle.applied_controls[-1].steer == pytest.approx(sign * 0.001 / 0.995)
    assert json.loads(records[-1].payload_json)["effective"]["steering"] == small.steering
    input_log = json.loads(next(r.payload_json for r in records
                               if r.event_type == "tsxw_input_sample"))
    assert input_log["effective"]["steering_engaged"] is False
    neutral = frame(PILOT_MANUAL_STEERING_CENTER_AXIS)
    assert neutral.steering == 0.0
    assert neutral.steering_engaged is False
    assert vehicle.applied_controls[-1].steer == 0.0

    controller.update(DriverInput(activation_requested=True))
    sent = len(vehicle.applied_controls)
    for raw in (sign * 0.005, sign * 0.01, sign * 0.02):
        frame(raw)
        assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE
        assert len(vehicle.applied_controls) == sent

    takeover = frame(sign * 0.020001)
    assert takeover.steering_engaged is True
    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert len(vehicle.applied_controls) == sent + 1
    assert vehicle.applied_controls[-1].steer == pytest.approx(takeover.steering)
    assert [event.deactivation_reason for event in events
            if event.event_type is AutomationInteractionEventType.DISENGAGEMENT
            ] == [DeactivationReason.DRIVER_STEERING]

    # Existing keyboard priority, pedal brake precedence, and steer cap remain.
    keys[pygame.K_d] = True
    device.axes[1:3] = [0.5, -1.0]
    keyboard = frame(-0.01)
    control = vehicle.applied_controls[-1]
    assert control.steer == pytest.approx(keyboard.steering)
    assert control.steer > 0.0
    assert control.brake == pytest.approx(0.25)
    assert control.throttle == 0.0
    keys.clear()
    for raw in (-1.0, 1.0):
        frame(raw)
        assert vehicle.applied_controls[-1].steer == pytest.approx(raw * 0.15)
    view._close_input()


@pytest.mark.parametrize("trigger,reason", [
    ("brake", DeactivationReason.DRIVER_BRAKE),
    ("wheel_button", DeactivationReason.DRIVER_BUTTON),
    ("keyboard", DeactivationReason.DRIVER_STEERING),
])
def test_other_noa_disengagement_inputs_unchanged(monkeypatch, trigger, reason):
    controller, events = pilot_controller()
    view, device, vehicle, keys, _ = make_live_view(monkeypatch, controller)
    controller.update(DriverInput(activation_requested=True))
    device.axes[0] = 0.01  # Small manual steering must not mask another override.
    if trigger == "brake":
        device.axes[1] = 0.8
    elif trigger == "wheel_button":
        device.buttons[5] = 1
    else:
        keys[pygame.K_a] = True
    driver = view._collect_driver_input([])
    view._pending_driver_input = driver
    controller.update(driver)
    view._apply_manual_control()
    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert len(vehicle.applied_controls) == 1
    assert events[-1].deactivation_reason is reason
    view._close_input()
