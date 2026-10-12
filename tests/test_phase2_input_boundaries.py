from __future__ import annotations

from collections import defaultdict

import carla
import pygame
import pytest

from src.experiment.lane_change import LaneChangeDirection
from src.scenario.research_lane_change_input import LaneChangeRequestInput
from src.scenario.research_noa_view import ResearchDriverView
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeWorld


@pytest.fixture(autouse=True)
def keyboard_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))


def key_event(event_type: int, key: int, *, repeat: bool = False) -> pygame.event.Event:
    return pygame.event.Event(event_type, key=key, repeat=int(repeat))


def apply_batch(
    viewer: ResearchDriverView,
    events: list[pygame.event.Event],
) -> LaneChangeDirection | None:
    collected = viewer._collect_driver_input(events)
    viewer._handle_mode_events(events)
    return collected.lane_change_direction


def test_research_view_reuses_one_edge_for_request_and_toggle() -> None:
    vehicle = SignalVehicle()
    viewer = ResearchDriverView(FakeWorld(), vehicle)

    first = apply_batch(viewer, [key_event(pygame.KEYDOWN, pygame.K_x)])
    repeated = apply_batch(
        viewer,
        [key_event(pygame.KEYDOWN, pygame.K_x, repeat=True)],
    )
    held = apply_batch(viewer, [key_event(pygame.KEYDOWN, pygame.K_x)])

    assert first is LaneChangeDirection.RIGHT
    assert repeated is None
    assert held is None
    assert vehicle.current_light_state == carla.VehicleLightState.RightBlinker


def test_research_view_release_then_second_down_cancels_signal() -> None:
    vehicle = SignalVehicle()
    viewer = ResearchDriverView(FakeWorld(), vehicle)

    direction = apply_batch(
        viewer,
        [
            key_event(pygame.KEYDOWN, pygame.K_x),
            key_event(pygame.KEYUP, pygame.K_x),
            key_event(pygame.KEYDOWN, pygame.K_x),
        ],
    )

    assert direction is None
    assert vehicle.current_light_state == carla.VehicleLightState.NONE


def test_research_view_x_then_z_finishes_left_with_keyless_events() -> None:
    vehicle = SignalVehicle()
    viewer = ResearchDriverView(FakeWorld(), vehicle)

    direction = apply_batch(
        viewer,
        [
            pygame.event.Event(pygame.MOUSEMOTION, pos=(1, 2)),
            key_event(pygame.KEYDOWN, pygame.K_x),
            key_event(pygame.KEYDOWN, pygame.K_z),
            pygame.event.Event(pygame.QUIT),
        ],
    )

    assert direction is LaneChangeDirection.LEFT
    assert vehicle.current_light_state == carla.VehicleLightState.LeftBlinker


def test_signal_helper_ignores_keyless_events() -> None:
    result = LaneChangeRequestInput().direction(
        [pygame.event.Event(pygame.MOUSEMOTION, pos=(1, 2))],
        carla.VehicleLightState.NONE,
    )

    assert result is None


class SignalVehicle(FakeResearchVehicle):
    def set_light_state(self, state: carla.VehicleLightState) -> None:
        self.current_light_state = state
