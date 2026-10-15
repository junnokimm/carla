from __future__ import annotations

import json
from collections import defaultdict

import pygame
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction import DriverInput
from src.p8.tsxw_policy import (
    TsxwCommandState,
    TsxwConfig,
    TsxwInputSample,
    coalesce_command_events,
    merge_driver_input,
    normalize_pedal,
    normalize_steering,
)
from src.scenario.research_noa_view import (
    ResearchDriverView,
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeWorld


def sample(**changes: bool | float) -> TsxwInputSample:
    values: dict[str, bool | float] = {
        "raw_steering": 0.25,
        "raw_brake": 0.5,
        "raw_throttle": -0.5,
        "steering": 0.25,
        "brake": 0.25,
        "throttle": 0.75,
        "steering_engaged": True,
        "left_pressed": False,
        "right_pressed": False,
        "noa_pressed": False,
        "reject_pressed": False,
        "left_held": False,
        "right_held": False,
        "noa_held": False,
        "reject_held": False,
    }
    values.update(changes)
    return TsxwInputSample(**values)


def test_axis_normalization_preserves_simultaneous_pedals_and_provisional_brake() -> (
    None
):
    assert normalize_pedal(-1.0, pressed_axis=-1.0) == pytest.approx(1.0)
    assert normalize_pedal(1.0, pressed_axis=-1.0) == pytest.approx(0.0)
    assert normalize_pedal(0.0, pressed_axis=-1.0) == pytest.approx(0.5)
    assert normalize_pedal(-0.639, pressed_axis=-1.0) == pytest.approx(0.8195)


def test_steering_calibration_and_deadzone_are_explicit() -> None:
    config = TsxwConfig(index=0, steering_deadzone=0.1)

    assert normalize_steering(-1.0, config) == pytest.approx(-1.0)
    assert normalize_steering(-0.05, config) == 0.0
    assert normalize_steering(0.55, config) == pytest.approx(0.5)


def test_keyboard_override_is_per_control_and_release_restores_wheel() -> None:
    wheel = sample()
    keyboard = DriverInput(throttle=1.0, brake=1.0, steering=-0.4, hand_brake=True)

    overridden = merge_driver_input(
        keyboard,
        wheel,
        throttle_override=True,
        brake_override=False,
        steering_override=True,
    )
    restored = merge_driver_input(
        DriverInput(),
        wheel,
        throttle_override=False,
        brake_override=False,
        steering_override=False,
    )

    assert overridden.throttle == 1.0
    assert overridden.brake == pytest.approx(0.25)
    assert overridden.steering == pytest.approx(-0.4)
    assert overridden.hand_brake is True
    assert restored == DriverInput(
        throttle=0.75,
        brake=0.25,
        steering=0.25,
        steering_engaged=True,
    )


def test_command_coalescing_emits_one_edge_for_keyboard_and_wheel_same_frame() -> None:
    state = TsxwCommandState()
    keyboard_down = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)

    events = coalesce_command_events(
        state,
        [keyboard_down],
        sample(noa_pressed=True, noa_held=True),
        noa_key=pygame.K_n,
    )
    held = coalesce_command_events(
        state,
        [],
        sample(noa_held=True),
        noa_key=pygame.K_n,
    )

    assert [(event.type, event.key) for event in events] == [
        (pygame.KEYDOWN, pygame.K_n)
    ]
    assert held == []


def test_command_release_waits_for_both_keyboard_and_wheel() -> None:
    state = TsxwCommandState()
    coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x, repeat=0)],
        sample(right_pressed=True, right_held=True),
        noa_key=pygame.K_n,
    )

    keyboard_up = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYUP, key=pygame.K_x)],
        sample(right_held=True),
        noa_key=pygame.K_n,
    )
    wheel_up = coalesce_command_events(
        state,
        [],
        sample(),
        noa_key=pygame.K_n,
    )

    assert keyboard_up == []
    assert [(event.type, event.key) for event in wheel_up] == [
        (pygame.KEYUP, pygame.K_x)
    ]


def test_command_source_handoff_in_one_frame_emits_no_false_edges() -> None:
    state = TsxwCommandState()
    coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)],
        sample(),
        noa_key=pygame.K_n,
    )

    events = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYUP, key=pygame.K_n)],
        sample(noa_pressed=True, noa_held=True),
        noa_key=pygame.K_n,
    )

    assert events == []


def test_command_wheel_to_keyboard_handoff_emits_no_false_edges() -> None:
    state = TsxwCommandState()
    coalesce_command_events(
        state,
        [],
        sample(reject_pressed=True, reject_held=True),
        noa_key=pygame.K_n,
    )

    events = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r, repeat=0)],
        sample(),
        noa_key=pygame.K_n,
    )

    assert events == []


def test_research_view_logs_raw_normalized_and_effective_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_s] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    monkeypatch.setattr(pygame.key, "get_focused", lambda: False)
    records = []
    viewer = ResearchDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(
            tsxw_event_sink=records.append,
        ),
    )
    wheel_sample = sample(right_pressed=True, right_held=True)

    class Input:
        def poll(self, events=()) -> TsxwInputSample:
            return wheel_sample

    viewer._tsxw_input = Input()

    collected = viewer._collect_driver_input([])

    assert collected.brake == 1.0
    assert collected.throttle == pytest.approx(0.75)
    assert collected.lane_change_direction is not None
    assert len(records) == 1
    assert '"raw":{"brake":0.5' in records[0].payload_json
    assert '"effective":{"brake":1.0' in records[0].payload_json
    assert '"window_input_focus":false' in records[0].payload_json


def test_manual_control_log_records_exact_positive_brake_suppression() -> None:
    records = []
    vehicle = FakeResearchVehicle()
    viewer = ResearchDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0),
            tsxw_event_sink=records.append,
        ),
    )
    viewer._tsxw_input = object()
    viewer._pending_driver_input = DriverInput(throttle=1.0, brake=1e-12)

    ResearchLiveDriverView._apply_manual_control(viewer)

    assert records[-1].event_type == "tsxw_manual_control"
    assert json.loads(records[-1].payload_json) == {
        "sent": {
            "brake": 9.999999960041972e-13,
            "gear": 1,
            "hand_brake": False,
            "manual_gear_shift": False,
            "reverse": False,
            "steering": 0.0,
            "throttle": 0.0,
            "transport": "VEHICLE_APPLY_CONTROL",
        },
        "effective": {
            "brake": 1e-12,
            "hand_brake": False,
            "steering": 0.0,
            "throttle": 1.0,
        },
        "throttle_suppression": "POSITIVE_BRAKE",
    }


def test_live_manual_w_with_zero_brake_sends_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_w] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    vehicle = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0)
        ),
    )
    wheel_sample = sample(brake=0.0)

    class Input:
        def poll(self, events=()) -> TsxwInputSample:
            return wheel_sample

    viewer._tsxw_input = Input()
    viewer._pending_driver_input = viewer._collect_driver_input([])

    viewer._apply_manual_control()

    sent = vehicle.applied_controls[-1]
    assert sent.throttle == pytest.approx(1.0)
    assert sent.brake == 0.0


def test_live_manual_physical_accelerator_with_zero_brake_sends_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    vehicle = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0)
        ),
    )
    wheel_sample = sample(brake=0.0, throttle=0.75)

    class Input:
        def poll(self, events=()) -> TsxwInputSample:
            return wheel_sample

    viewer._tsxw_input = Input()
    viewer._pending_driver_input = viewer._collect_driver_input([])

    viewer._apply_manual_control()

    sent = vehicle.applied_controls[-1]
    assert sent.throttle == pytest.approx(0.75)
    assert sent.brake == 0.0


def test_live_manual_space_retains_hand_brake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_SPACE] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    vehicle = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0)
        ),
    )
    viewer._pending_driver_input = viewer._collect_driver_input([])

    viewer._apply_manual_control()

    assert vehicle.applied_controls[-1].hand_brake is True


def test_live_manual_skips_writer_while_noa_owns_control() -> None:
    vehicle = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0),
            automation_state_source=lambda: AutomationState(
                AutomationAvailability.AVAILABLE,
                DrivingControlMode.NOA_ACTIVE,
            ),
        ),
    )
    viewer._pending_driver_input = DriverInput(throttle=1.0)

    viewer._apply_manual_control()

    assert vehicle.applied_controls == []


def test_manual_control_log_identifies_configured_checked_batch_transport() -> None:
    records = []
    sent_controls = []
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0),
            control_sender=sent_controls.append,
            tsxw_event_sink=records.append,
        ),
    )
    viewer._tsxw_input = object()
    viewer._pending_driver_input = DriverInput(throttle=0.5)

    viewer._apply_manual_control()

    payload = json.loads(records[-1].payload_json)
    assert sent_controls[-1].throttle == pytest.approx(0.5)
    assert payload["sent"]["transport"] == "CHECKED_BATCH_SYNC"
