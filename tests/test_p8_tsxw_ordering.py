from __future__ import annotations

import pygame
import pytest

from src.p8.tsxw_input import TsxwInput
from src.p8.tsxw_policy import (
    TsxwCommandState,
    TsxwConfig,
    TsxwConfigError,
    TsxwInputSample,
    coalesce_command_events,
)
from tests.test_p8_tsxw_device import FakeJoystick, install_devices


def sample(**changes: bool | float) -> TsxwInputSample:
    values: dict[str, bool | float] = {
        "raw_steering": 0.0,
        "raw_brake": 1.0,
        "raw_throttle": 1.0,
        "steering": 0.0,
        "brake": 0.0,
        "throttle": 0.0,
        "steering_engaged": False,
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


def event_identity(events: list[pygame.event.Event]) -> list[tuple[int, int | None]]:
    return [(event.type, event.dict.get("key")) for event in events]


def test_keyboard_commands_and_noncommands_preserve_source_event_order() -> None:
    state = TsxwCommandState()
    events = [
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x, repeat=0),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v, repeat=0),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_z, repeat=0),
    ]

    combined = coalesce_command_events(state, events, sample(), noa_key=pygame.K_n)

    assert event_identity(combined) == [
        (pygame.KEYDOWN, pygame.K_x),
        (pygame.KEYDOWN, pygame.K_v),
        (pygame.KEYDOWN, pygame.K_z),
    ]


def test_release_press_release_after_prior_hold_emits_one_new_press() -> None:
    state = TsxwCommandState()
    coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)],
        sample(),
        noa_key=pygame.K_n,
    )

    combined = coalesce_command_events(
        state,
        [
            pygame.event.Event(pygame.KEYUP, key=pygame.K_n),
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0),
            pygame.event.Event(pygame.KEYUP, key=pygame.K_n),
        ],
        sample(),
        noa_key=pygame.K_n,
    )

    assert event_identity(combined) == [
        (pygame.KEYUP, pygame.K_n),
        (pygame.KEYDOWN, pygame.K_n),
        (pygame.KEYUP, pygame.K_n),
    ]


def test_focus_loss_clears_keyboard_hold_and_allows_next_wheel_press() -> None:
    state = TsxwCommandState()
    coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)],
        sample(),
        noa_key=pygame.K_n,
    )

    focus_events = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.WINDOWFOCUSLOST)],
        sample(),
        noa_key=pygame.K_n,
    )
    wheel_events = coalesce_command_events(
        state,
        [],
        sample(noa_pressed=True, noa_held=True),
        noa_key=pygame.K_n,
    )

    assert event_identity(focus_events) == [
        (pygame.WINDOWFOCUSLOST, None),
        (pygame.KEYUP, pygame.K_n),
    ]
    assert event_identity(wheel_events) == [(pygame.KEYDOWN, pygame.K_n)]
    assert state.keyboard_down == set()


def test_focus_loss_does_not_release_command_held_by_wheel() -> None:
    state = TsxwCommandState()
    coalesce_command_events(
        state,
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, repeat=0)],
        sample(noa_pressed=True, noa_held=True),
        noa_key=pygame.K_n,
    )

    focus_events = coalesce_command_events(
        state,
        [pygame.event.Event(pygame.WINDOWFOCUSLOST)],
        sample(noa_held=True),
        noa_key=pygame.K_n,
    )
    released = coalesce_command_events(
        state,
        [],
        sample(),
        noa_key=pygame.K_n,
    )

    assert event_identity(focus_events) == [(pygame.WINDOWFOCUSLOST, None)]
    assert event_identity(released) == [(pygame.KEYUP, pygame.K_n)]
    assert state.keyboard_down == set()


@pytest.mark.parametrize("noa_key", [pygame.K_r, pygame.K_z, pygame.K_x])
def test_noa_key_collision_is_rejected_before_state_changes(noa_key: int) -> None:
    state = TsxwCommandState()

    with pytest.raises(TsxwConfigError, match="must differ"):
        coalesce_command_events(state, [], sample(), noa_key=noa_key)

    assert state.keyboard_down == set()
    assert state.wheel_down == set()


def test_open_cleanup_retains_log_error_when_device_quit_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeJoystick(0)
    device.quit_error = pygame.error("device quit failed")
    operations = install_devices(monkeypatch, [device])

    def fail_log(event) -> None:
        raise OSError("log unavailable")

    with pytest.raises(OSError, match="log unavailable") as caught:
        TsxwInput.open(
            TsxwConfig(name="TS-XW Racer", steering_deadzone=0.0),
            event_sink=fail_log,
        )

    assert operations == ["module.init", "module.quit"]
    assert device.quit_count == 1
    assert any("device quit failed" in note for note in caught.value.__notes__)
