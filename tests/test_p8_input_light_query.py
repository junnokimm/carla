from collections import defaultdict
from dataclasses import replace
from types import SimpleNamespace

import carla
import pygame
import pytest

from src.experiment.lane_change import LaneChangeDirection
from src.scenario.camera_feed import CameraFeed
from src.scenario.research_exit_runtime import ResearchExitViewBinding
from src.scenario.research_noa_view import ResearchDriverView
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeSensor, FakeWorld
from tests.test_p5_exit_assistance import ACTIVE, MANUAL
from tests.test_p5_real_driver_view_binding import BindingHarness, present_frame
from tests.test_p8_tsxw_regressions import sample


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    harness = BindingHarness(ACTIVE)
    binding = ResearchExitViewBinding(
        harness.observer,
        harness.persistence,
        replace(
            harness.binding.config,
            confirm_key="x",
            reject_key="r",
            indicator_confirmation=True,
        ),
    )
    hero = FakeResearchVehicle()
    view = ResearchDriverView(FakeWorld(), hero)
    view._feeds = [CameraFeed("front", FakeSensor())]
    view.set_exit_view_binding(binding)
    return harness, hero, view


@pytest.mark.parametrize("mode", [MANUAL, ACTIVE])
@pytest.mark.parametrize("key", [None, pygame.K_w, pygame.K_n, pygame.K_r])
def test_no_indicator_event_does_not_query_unused_light_state(
    rig, monkeypatch, mode, key
):
    harness, hero, view = rig
    harness.interaction.state = mode
    present_frame(view)
    calls = []
    monkeypatch.setattr(
        hero,
        "get_light_state",
        lambda: calls.append(True) or carla.VehicleLightState.RightBlinker,
    )
    events = [] if key is None else [pygame.event.Event(pygame.KEYDOWN, key=key)]

    collected = view._collect_driver_input(events)

    assert calls == []
    assert not collected.exit_confirm_requested
    assert collected.lane_change_direction is None
    assert collected.activation_requested is (key == pygame.K_n)
    assert collected.exit_reject_requested is (mode == ACTIVE and key == pygame.K_r)


@pytest.mark.parametrize("key", [pygame.K_z, pygame.K_x])
@pytest.mark.parametrize("event_type", [pygame.KEYDOWN, pygame.KEYUP])
def test_indicator_query_failure_still_propagates(rig, monkeypatch, key, event_type):
    _, hero, view = rig

    def failed_query():
        raise RuntimeError("light RPC failed")

    monkeypatch.setattr(hero, "get_light_state", failed_query)
    with pytest.raises(RuntimeError, match="light RPC failed"):
        view._collect_driver_input([pygame.event.Event(event_type, key=key)])


def test_fresh_indicator_state_is_used_after_idle_and_release(rig, monkeypatch):
    _, hero, view = rig
    present_frame(view)
    calls = []

    def query():
        calls.append(hero.current_light_state)
        return hero.current_light_state

    monkeypatch.setattr(hero, "get_light_state", query)
    down = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x)
    up = pygame.event.Event(pygame.KEYUP, key=pygame.K_x)
    for _ in range(3):
        view._collect_driver_input([])
    first = view._collect_driver_input([down])
    hero.current_light_state = carla.VehicleLightState.RightBlinker
    repeated = view._collect_driver_input([down])
    view._collect_driver_input([up])
    toggle_off = view._collect_driver_input([down])
    assert len(calls) == 4
    assert first.exit_confirm_requested
    assert first.lane_change_direction is LaneChangeDirection.RIGHT
    assert not repeated.exit_confirm_requested
    assert not toggle_off.exit_confirm_requested
    assert toggle_off.lane_change_direction is None


def test_wheel_synthesized_indicator_edge_still_queries_and_confirms(rig, monkeypatch):
    _, hero, view = rig
    present_frame(view)
    samples = iter(
        [
            sample(steering=0.0, steering_engaged=False),
            sample(
                steering=0.0,
                steering_engaged=False,
                right_pressed=True,
                right_held=True,
            ),
        ]
    )
    view._tsxw_input = SimpleNamespace(poll=lambda events: next(samples))
    calls = []
    monkeypatch.setattr(
        hero,
        "get_light_state",
        lambda: calls.append(True) or carla.VehicleLightState.NONE,
    )
    idle = view._collect_driver_input([])
    assert calls == []
    assert idle.throttle == 0.6
    pressed = view._collect_driver_input([])
    assert calls == [True]
    assert pressed.exit_confirm_requested
    assert pressed.lane_change_direction is LaneChangeDirection.RIGHT
