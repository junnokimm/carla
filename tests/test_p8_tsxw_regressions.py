from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager

import pygame
import pytest

from src.p8.tsxw_input import TSXW_NAME, TsxwDeviceError, TsxwInput
from src.p8.tsxw_policy import (
    TsxwCommandState,
    TsxwConfig,
    TsxwConfigError,
    TsxwInputSample,
    coalesce_command_events,
)
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeWorld
from tests.test_p8_tsxw_device import FakeJoystick, install_devices


def sample(**changes: bool | float) -> TsxwInputSample:
    values: dict[str, bool | float] = {
        "raw_steering": 0.0,
        "raw_brake": 1.0,
        "raw_throttle": 1.0,
        "steering": -0.7,
        "brake": 0.0,
        "throttle": 0.6,
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


@pytest.mark.parametrize("key", [pygame.K_n, pygame.K_z, pygame.K_x, pygame.K_r])
def test_quick_keyboard_tap_preserves_one_command_when_wheel_is_idle(key: int) -> None:
    state = TsxwCommandState()

    events = coalesce_command_events(
        state,
        [
            pygame.event.Event(pygame.KEYDOWN, key=key, repeat=0),
            pygame.event.Event(pygame.KEYUP, key=key),
        ],
        sample(),
        noa_key=pygame.K_n,
    )

    assert [(event.type, event.key) for event in events] == [
        (pygame.KEYDOWN, key),
        (pygame.KEYUP, key),
    ]
    assert key not in state.keyboard_down


def test_keyboard_repeat_does_not_emit_another_command_edge() -> None:
    state = TsxwCommandState()
    initial = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)],
        sample(),
        noa_key=pygame.K_n,
    )

    repeated = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=1)],
        sample(),
        noa_key=pygame.K_n,
    )

    assert [(event.type, event.key) for event in initial] == [
        (pygame.KEYDOWN, pygame.K_n)
    ]
    assert repeated == []


@pytest.mark.parametrize(
    ("button", "pressed_field", "held_field"),
    [
        (9, "left_pressed", "left_held"),
        (8, "right_pressed", "right_held"),
        (5, "noa_pressed", "noa_held"),
        (7, "reject_pressed", "reject_held"),
    ],
)
def test_each_wheel_button_emits_one_press_and_one_release_state(
    monkeypatch: pytest.MonkeyPatch,
    button: int,
    pressed_field: str,
    held_field: str,
) -> None:
    device = FakeJoystick(0)
    install_devices(monkeypatch, [device])
    adapter = TsxwInput.open(TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0))
    device.buttons[button] = 1

    pressed = adapter.poll()
    held = adapter.poll()
    device.buttons[button] = 0
    released = adapter.poll()
    adapter.close()

    assert getattr(pressed, pressed_field) is True
    assert getattr(pressed, held_field) is True
    assert getattr(held, pressed_field) is False
    assert getattr(held, held_field) is True
    assert getattr(released, pressed_field) is False
    assert getattr(released, held_field) is False


def test_open_without_matching_device_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = install_devices(monkeypatch, [])

    with pytest.raises(TsxwDeviceError, match="no matching TS-XW device"):
        TsxwInput.open(TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0))

    assert operations == ["module.init", "module.quit"]


def test_name_discovery_selects_tsxw_at_nonzero_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = FakeJoystick(0, name="Other")
    device = FakeJoystick(1)
    install_devices(monkeypatch, [other, device])
    records = []

    adapter = TsxwInput.open(
        TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0),
        event_sink=records.append,
    )
    adapter.close()

    assert other.get_init() is False
    assert device.quit_count == 1
    assert json.loads(records[0].payload_json)["index"] == 1


def test_explicit_reopen_starts_with_fresh_button_edge_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeJoystick(0)
    install_devices(monkeypatch, [first])
    first_adapter = TsxwInput.open(TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0))
    first.buttons[5] = 1
    assert first_adapter.poll().noa_pressed is True
    first_adapter.close()

    replacement = FakeJoystick(0)
    install_devices(monkeypatch, [replacement])
    reopened = TsxwInput.open(TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0))
    neutral = reopened.poll()
    replacement.buttons[5] = 1
    pressed = reopened.poll()
    reopened.close()

    assert neutral.noa_pressed is False
    assert neutral.noa_held is False
    assert pressed.noa_pressed is True


@contextmanager
def runner_boundary() -> Iterator[None]:
    yield


@pytest.mark.parametrize(
    "error",
    [TsxwDeviceError("device removed"), TsxwConfigError("bad calibration")],
)
def test_tsxw_errors_propagate_through_runner_style_contextmanager(
    error: TsxwDeviceError | TsxwConfigError,
) -> None:
    with pytest.raises(type(error), match=str(error)) as caught, runner_boundary():
        raise error

    assert caught.value is error


def test_live_manual_control_applies_keyboard_priority_and_brake_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_w] = True
    keys[pygame.K_s] = True
    keys[pygame.K_d] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    vehicle = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(0.6, 0.8, 0.5)
        ),
    )

    class Input:
        def poll(self, events=()) -> TsxwInputSample:
            return sample()

    viewer._tsxw_input = Input()
    viewer._pending_driver_input = viewer._collect_driver_input([])

    viewer._apply_manual_control()

    applied = vehicle.applied_controls[-1]
    assert applied.throttle == 0.0
    assert applied.brake == pytest.approx(0.8)
    assert applied.steer > 0.0
