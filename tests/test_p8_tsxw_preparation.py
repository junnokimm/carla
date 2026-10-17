from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

import pygame
import pytest

from src.p8 import tsxw_preparation
from src.p8.tsxw_input import TsxwDeviceError, TsxwInput, TsxwLogEvent
from src.p8.tsxw_policy import SDL_AXIS_POSITIVE_ENDPOINT, TsxwConfig
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeWorld
from tests.test_p8_tsxw_device import FakeJoystick, install_devices


@dataclass(frozen=True)
class Frame:
    brake: float = SDL_AXIS_POSITIVE_ENDPOINT
    throttle: float = SDL_AXIS_POSITIVE_ENDPOINT
    focused: bool = True
    keys: tuple[int, ...] = ()
    buttons: tuple[int, ...] = ()
    events: tuple[pygame.event.Event, ...] = ()


def setup_view(monkeypatch: pytest.MonkeyPatch, frames: list[Frame]):
    device = FakeJoystick(0)
    install_devices(monkeypatch, [device])
    pending_frames = iter(frames)
    keys: defaultdict[int, bool] = defaultdict(bool)
    state = {"focused": True, "polls": 0}
    pages = []
    records: list[TsxwLogEvent] = []

    def poll_events():
        frame = next(pending_frames)
        state["polls"] += 1
        state["focused"] = frame.focused
        device.axes[1:3] = [frame.brake, frame.throttle]
        device.buttons[:] = [int(i in frame.buttons) for i in range(13)]
        keys.clear()
        keys.update(dict.fromkeys(frame.keys, True))
        return list(frame.events)

    class Renderer:
        def __init__(self, size) -> None:
            self.size = size

        def draw(self, screen, page) -> None:
            pages.append(page)

    class Clock:
        def tick(self, rate) -> None:
            return None

    monkeypatch.setattr(pygame.event, "get", poll_events)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    monkeypatch.setattr(pygame.key, "get_focused", lambda: state["focused"])
    monkeypatch.setattr(pygame.time, "Clock", Clock)
    monkeypatch.setattr(tsxw_preparation, "OperatorRenderer", Renderer)
    vehicle = FakeResearchVehicle()
    view = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            tsxw_config=TsxwConfig(index=0, steering_deadzone=0.02),
            tsxw_event_sink=records.append,
            manual_control_limits=ResearchManualControlLimits(1.0, 0.5, 0.15),
        ),
    )
    return view, vehicle, device, keys, state, pages, records


def payload(records: list[TsxwLogEvent], event_type: str) -> dict:
    return json.loads(next(r.payload_json for r in records if r.event_type == event_type))


def test_released_pedals_prepare_without_reopening_device_or_recreating_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pygame.quit()
    view, vehicle, device, _, state, _, records = setup_view(
        monkeypatch, [Frame(), Frame()]
    )
    created = []
    original_set_mode = pygame.display.set_mode

    def set_mode(size):
        created.append(size)
        return original_set_mode(size)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    try:
        view.prepare_input()
        adapter = view._tsxw_input
        view.prepare_input()
        view._initialize_input()
        assert view._tsxw_input is adapter
        assert state["polls"] == 2
        assert all(c.throttle == 0.0 and c.brake == 0.5 for c in vehicle.applied_controls)
        monkeypatch.setattr(
            pygame.event, "get", lambda: [pygame.event.Event(pygame.QUIT)]
        )

        assert view.run(600) is True

        assert len(created) == 1
        assert [r.event_type for r in records].count("tsxw_device_opened") == 1
        assert device.quit_count == 1
        assert view._tsxw_input is None
        assert not pygame.display.get_init()
    finally:
        view.close()
        pygame.quit()


def test_initial_zero_pedals_and_discontinuous_release_wait_with_brake_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = [
        Frame(brake=0.0, throttle=0.0),
        Frame(),
        Frame(brake=0.0),
        Frame(),
        Frame(),
    ]
    view, vehicle, _, _, state, pages, records = setup_view(monkeypatch, frames)
    try:
        view.prepare_input()

        assert state["polls"] == 5
        assert len(vehicle.applied_controls) == 5
        assert all(c.throttle == 0.0 and c.brake == 0.5 for c in vehicle.applied_controls)
        assert pages[0].controls[:2] == ("가속 페달: 확인 대기", "브레이크 페달: 확인 대기")
        ready = payload(records, "tsxw_input_preparation_ready")
        assert ready["sample_count"] == 5
        assert ready["raw"]["brake"] == SDL_AXIS_POSITIVE_ENDPOINT
    finally:
        view.close()


@pytest.mark.parametrize("use_keyboard", [False, True])
def test_ready_handle_delivers_acceleration_and_preserves_real_half_brake(
    monkeypatch: pytest.MonkeyPatch,
    use_keyboard: bool,
) -> None:
    view, vehicle, device, keys, _, _, _ = setup_view(monkeypatch, [Frame(), Frame()])
    try:
        view.prepare_input()
        device.axes[2] = SDL_AXIS_POSITIVE_ENDPOINT if use_keyboard else -1.0
        keys[pygame.K_w] = use_keyboard
        view._pending_driver_input = view._collect_driver_input([])
        view._apply_manual_control()
        assert vehicle.applied_controls[-1].throttle == 1.0
        assert vehicle.applied_controls[-1].brake == 0.0

        device.axes[1] = 0.0
        view._pending_driver_input = view._collect_driver_input([])
        view._apply_manual_control()

        assert vehicle.applied_controls[-1].throttle == 0.0
        assert vehicle.applied_controls[-1].brake == 0.5
    finally:
        view.close()


@pytest.mark.parametrize(
    "held_frame",
    [
        Frame(focused=False),
        Frame(keys=(pygame.K_SPACE,)),
        Frame(keys=(pygame.K_w,)),
        Frame(keys=(pygame.K_n, pygame.K_x)),
        Frame(buttons=(5, 8)),
    ],
)
def test_focus_and_control_release_are_required_before_driving(
    monkeypatch: pytest.MonkeyPatch,
    held_frame: Frame,
) -> None:
    view, _, _, _, state, _, records = setup_view(
        monkeypatch, [held_frame, held_frame, Frame(), Frame()]
    )
    try:
        view.prepare_input()
        collected = view._collect_driver_input([])

        assert state["polls"] == 4
        assert not collected.hand_brake
        assert not collected.activation_requested
        assert not collected.deactivation_requested
        assert collected.lane_change_direction is None
        assert payload(records, "tsxw_input_preparation_ready")["controls_released"]
    finally:
        view.close()


@pytest.mark.parametrize(
    "exit_event",
    [pygame.event.Event(pygame.QUIT), pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],
)
def test_cancel_records_last_raw_sample_and_closes_input_and_display(
    monkeypatch: pytest.MonkeyPatch,
    exit_event: pygame.event.Event,
) -> None:
    view, _, device, _, _, _, records = setup_view(
        monkeypatch, [Frame(brake=0.0, throttle=0.0), Frame(events=(exit_event,))]
    )

    with pytest.raises(TsxwDeviceError, match=tsxw_preparation.CANCELLED):
        view.prepare_input()

    cancelled = payload(records, "tsxw_input_preparation_cancelled")
    assert cancelled["raw"]["brake"] == 0.0
    assert cancelled["raw"]["throttle"] == 0.0
    assert device.quit_count == 1
    assert view._tsxw_input is None
    assert not pygame.display.get_init()
    view.close()


def test_disconnect_during_preparation_fails_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removal = pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=100)
    view, _, device, _, _, _, records = setup_view(
        monkeypatch, [Frame(brake=0.0), Frame(events=(removal,))]
    )

    with pytest.raises(TsxwDeviceError, match="device removed"):
        view.prepare_input()

    assert payload(records, "tsxw_input_preparation_failed")["raw"]["brake"] == 0.0
    assert device.quit_count == 1
    assert not pygame.display.get_init()
    view.close()


def test_next_drive_reopen_must_confirm_its_own_initial_pedal_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _, first_device, _, _, _, _ = setup_view(monkeypatch, [Frame(), Frame()])
    view.prepare_input()
    view.close()
    assert first_device.quit_count == 1
    next_view, vehicle, next_device, _, state, _, records = setup_view(
        monkeypatch, [Frame(brake=0.0, throttle=0.0), Frame(), Frame()]
    )
    try:
        next_view.prepare_input()

        assert state["polls"] == 3
        assert all(c.throttle == 0.0 for c in vehicle.applied_controls)
        assert payload(records, "tsxw_input_preparation_ready")["sample_count"] == 3
    finally:
        next_view.close()
    assert next_device.quit_count == 1


def test_keyboard_only_prepare_input_has_no_device_display_or_control_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("keyboard-only setup must not touch SDL or TS-XW")

    monkeypatch.setattr(TsxwInput, "open", forbidden)
    monkeypatch.setattr(pygame.display, "init", forbidden)
    vehicle = FakeResearchVehicle()
    view = ResearchLiveDriverView(FakeWorld(), vehicle)

    view.prepare_input()

    assert vehicle.applied_controls == []
