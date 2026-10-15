from __future__ import annotations

import json
from collections import defaultdict

import carla
import pygame
import pytest

from src.p8.tsxw_input import TSXW_NAME, TsxwInput, TsxwLogEvent
from src.p8.tsxw_policy import TsxwConfig
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeWorld
from tests.test_p8_tsxw_device import FakeJoystick, install_devices

SDL_AXIS_DENOMINATOR = 32768
SDL_AXIS_POSITIVE_ENDPOINT = 32767 / SDL_AXIS_DENOMINATOR


def run_brake_sample(
    monkeypatch: pytest.MonkeyPatch,
    raw_brake: float,
) -> tuple[list[TsxwLogEvent], list[carla.VehicleControl]]:
    device = FakeJoystick(0, axes=[0.0, raw_brake, 1.0, 0.0])
    install_devices(monkeypatch, [device])
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_w] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    monkeypatch.setattr(pygame.key, "get_focused", lambda: False)
    records: list[TsxwLogEvent] = []
    sent_controls: list[carla.VehicleControl] = []
    adapter = TsxwInput.open(
        TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0),
        event_sink=records.append,
    )
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0),
            control_sender=sent_controls.append,
            tsxw_event_sink=records.append,
        ),
    )
    viewer._tsxw_input = adapter

    viewer._pending_driver_input = viewer._collect_driver_input([])
    viewer._apply_manual_control()
    adapter.close()

    return records, sent_controls


def payload_for(records: list[TsxwLogEvent], event_type: str) -> dict:
    event = next(record for record in records if record.event_type == event_type)
    return json.loads(event.payload_json)


def test_w_sends_throttle_when_released_brake_is_positive_sdl_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, sent_controls = run_brake_sample(
        monkeypatch,
        SDL_AXIS_POSITIVE_ENDPOINT,
    )

    sample_payload = payload_for(records, "tsxw_input_sample")
    manual_payload = payload_for(records, "tsxw_manual_control")
    sent = sent_controls[-1]
    assert sample_payload["raw"]["brake"] == SDL_AXIS_POSITIVE_ENDPOINT
    assert sample_payload["normalized"]["brake"] == 0.0
    assert sample_payload["normalized"]["throttle"] == 0.0
    assert sample_payload["effective"]["throttle"] == 1.0
    assert sample_payload["window_input_focus"] is False
    assert sent.throttle == pytest.approx(1.0)
    assert sent.brake == 0.0
    assert manual_payload["sent"]["transport"] == "CHECKED_BATCH_SYNC"
    assert manual_payload["throttle_suppression"] is None


@pytest.mark.parametrize(
    ("raw_brake", "expected_brake"),
    [
        ((SDL_AXIS_DENOMINATOR - 2) / SDL_AXIS_DENOMINATOR, 1 / 32768),
        (0.0, 0.5),
        (-1.0, 1.0),
    ],
)
def test_representable_brake_below_endpoint_still_suppresses_throttle(
    monkeypatch: pytest.MonkeyPatch,
    raw_brake: float,
    expected_brake: float,
) -> None:
    records, sent_controls = run_brake_sample(monkeypatch, raw_brake)

    sample_payload = payload_for(records, "tsxw_input_sample")
    manual_payload = payload_for(records, "tsxw_manual_control")
    sent = sent_controls[-1]
    assert sample_payload["raw"]["brake"] == raw_brake
    assert sample_payload["normalized"]["brake"] == pytest.approx(expected_brake)
    assert sample_payload["effective"]["throttle"] == 1.0
    assert sent.throttle == 0.0
    assert sent.brake == pytest.approx(expected_brake)
    assert manual_payload["throttle_suppression"] == "POSITIVE_BRAKE"
