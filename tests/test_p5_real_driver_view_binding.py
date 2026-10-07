from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pygame
import pytest

from src.experiment.automation import AutomationState
from src.experiment.context import ExperimentModule, Module1Condition
from src.experiment.exit_assistance import (
    ExitAssistanceStatus,
)
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.scenario.driver_view import CameraFeed
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_exit_runtime import (
    ResearchExitInteractionObserver,
    ResearchExitViewBinding,
)
from src.scenario.research_noa_view import ResearchDriverView
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeSensor, FakeWorld
from tests.test_p5_exit_assistance import (
    ACTIVE,
    MANUAL,
    ReferenceRecorder,
    coordinator,
    observation,
)


@pytest.fixture(autouse=True)
def keyboard_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))


class MutableInteraction:
    def __init__(self, state: AutomationState) -> None:
        self.state = state


class TimestampSource:
    def __init__(self) -> None:
        self._next_value = 30

    def get_host_timestamp(self) -> TimestampEnvelope:
        value = self._next_value
        self._next_value += 10
        return TimestampEnvelope(HostClockTimestamp(value, value + 1000))


class TimestampPersistence:
    def __init__(self) -> None:
        self.source = TimestampSource()


class BindingHarness:
    def __init__(self, state: AutomationState) -> None:
        self.coordinator = coordinator(
            ExperimentModule.MODULE_1,
            Module1Condition.SURT,
            ReferenceRecorder([]),
        )
        self.coordinator.observe(observation(250.0))
        self.interaction = MutableInteraction(state)
        self.persistence = TimestampPersistence()
        self.observer = ResearchExitInteractionObserver(
            self.interaction,
            self.coordinator,
            None,
            self.persistence,
        )
        self.binding = ResearchExitViewBinding(
            self.observer,
            self.persistence,
            ResearchExitAssistanceConfig(
                Path("unused.json"),
                self.coordinator.config.route.route_id,
                self.coordinator.config.lc_event_id,
            ),
        )


def make_view(binding: ResearchExitViewBinding) -> ResearchDriverView:
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)
    return viewer


def present_frame(viewer: ResearchDriverView) -> None:
    viewer._prepare_frame()
    viewer._draw(pygame.Surface((1280, 720)))
    viewer._after_display_flip()


def test_real_binding_marks_manual_t0_once_across_repeated_frames() -> None:
    harness = BindingHarness(MANUAL)
    viewer = make_view(harness.binding)

    present_frame(viewer)
    harness.coordinator.observe(observation(260.0, frame=2))
    present_frame(viewer)
    present_frame(viewer)

    assert harness.coordinator.status is ExitAssistanceStatus.PRESENTED
    presented = [
        event
        for event in harness.coordinator.events
        if event.event_type == "lc_presented"
    ]
    assert len(presented) == 1
    payload = json.loads(presented[0].payload_json)
    assert payload["t0_host_monotonic_ns"] == 30
    assert payload["noa_state_t0"] == "MANUAL"


def test_real_binding_does_not_remark_replacement_after_mode_change() -> None:
    harness = BindingHarness(ACTIVE)
    viewer = make_view(harness.binding)
    present_frame(viewer)

    harness.interaction.state = MANUAL
    present_frame(viewer)
    present_frame(viewer)

    assert (
        sum(event.event_type == "lc_presented" for event in harness.coordinator.events)
        == 1
    )


def test_posted_keydown_without_repeat_is_accepted_after_presentation() -> None:
    harness = BindingHarness(ACTIVE)
    viewer = make_view(harness.binding)
    present_frame(viewer)

    collected = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    )

    assert collected.exit_confirm_requested is True


def test_real_binding_activates_only_after_flip_and_hud_visibility() -> None:
    harness = BindingHarness(ACTIVE)
    viewer = make_view(harness.binding)
    viewer._prepare_frame()
    viewer._draw(pygame.Surface((1280, 720)))

    before_flip = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    )
    viewer._after_display_flip()
    after_flip = viewer._collect_driver_input(
        [
            pygame.event.Event(pygame.KEYUP, key=pygame.K_c),
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c),
        ]
    )
    viewer._hud_enabled = False
    viewer._prepare_frame()
    viewer._draw(pygame.Surface((1280, 720)))
    viewer._after_display_flip()
    hidden = viewer._collect_driver_input(
        [
            pygame.event.Event(pygame.KEYUP, key=pygame.K_c),
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c),
        ]
    )

    assert before_flip.exit_confirm_requested is False
    assert after_flip.exit_confirm_requested is True
    assert hidden.exit_confirm_requested is False


def test_key_edges_follow_batch_order_and_preserve_both_requests() -> None:
    harness = BindingHarness(ACTIVE)
    viewer = make_view(harness.binding)
    present_frame(viewer)

    first = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    )
    down_then_up = viewer._collect_driver_input(
        [
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c),
            pygame.event.Event(pygame.KEYUP, key=pygame.K_c),
        ]
    )
    both = viewer._collect_driver_input(
        [
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c),
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r),
        ]
    )

    assert first.exit_confirm_requested is True
    assert down_then_up.exit_confirm_requested is False
    assert both.exit_confirm_requested is True
    assert both.exit_reject_requested is True


def test_rebinding_resets_presentation_and_key_latches() -> None:
    first = BindingHarness(ACTIVE)
    viewer = make_view(first.binding)
    present_frame(viewer)
    viewer._collect_driver_input([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)])

    second = BindingHarness(ACTIVE)
    viewer.set_exit_view_binding(second.binding)
    present_frame(viewer)
    collected = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c)]
    )

    assert second.coordinator.status is ExitAssistanceStatus.PRESENTED
    assert collected.exit_confirm_requested is True
