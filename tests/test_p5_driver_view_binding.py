from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import pygame
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance import ExitAssistanceStatus, ExitPresentation
from src.scenario.driver_view import CameraFeed
from src.scenario.research_exit_runtime import ResearchExitViewBinding
from src.scenario.research_noa_view import ResearchDriverView, ResearchDriverViewConfig
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_driver_view import FakeSensor, FakeWorld
from tests.test_driver_view_runtime import FakeFrameClock, FakeTurnSignalAudio


@dataclass(frozen=True, slots=True)
class ExitKeys:
    confirm_key: str = "c"
    reject_key: str = "r"


class RecordingExitBinding:
    def __init__(self, presentation: ExitPresentation | None) -> None:
        self.config = ExitKeys()
        self.presentation = presentation
        self.marked: list[ExitPresentation] = []
        self.timeline: list[str] = []

    def prepare_presentation(self) -> ExitPresentation | None:
        return self.presentation

    def mark_presented(self, rendered: ExitPresentation) -> None:
        self.timeline.append("mark")
        self.marked.append(rendered)


def recommendation(*, assisted: bool = True) -> ExitPresentation:
    return ExitPresentation(
        "exit-1",
        "1.0 km 앞 출구, 우측 차로로",
        assisted,
        1000.0,
        42,
        7,
    )


def prepare_loop(
    monkeypatch: pytest.MonkeyPatch,
    viewer: ResearchDriverView,
    events: list[pygame.event.Event],
    timeline: list[str],
) -> None:
    from src.scenario import driver_view

    times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", FakeTurnSignalAudio)
    monkeypatch.setattr(driver_view.time, "monotonic", times.__next__)
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(
        driver_view.pygame.display,
        "set_mode",
        lambda size: pygame.Surface(size),
    )
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(
        driver_view.pygame.display,
        "flip",
        lambda: timeline.append("flip"),
    )
    monkeypatch.setattr(driver_view.pygame.event, "get", lambda: events)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", FakeFrameClock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    monkeypatch.setattr(
        driver_view.pygame.key,
        "get_pressed",
        lambda: defaultdict(bool),
    )
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)


def test_view_marks_exact_draft_immediately_after_successful_flip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = recommendation()
    binding = RecordingExitBinding(draft)
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)
    prepare_loop(monkeypatch, viewer, [], binding.timeline)

    viewer.run(1.0)

    assert binding.timeline == ["flip", "mark"]
    assert binding.marked == [draft]
    assert binding.marked[0] is draft


def test_view_does_not_mark_when_flip_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.scenario import driver_view

    binding = RecordingExitBinding(recommendation())
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)
    prepare_loop(monkeypatch, viewer, [], binding.timeline)
    monkeypatch.setattr(
        driver_view.pygame.display,
        "flip",
        lambda: (_ for _ in ()).throw(pygame.error("flip failed")),
    )

    with pytest.raises(pygame.error, match="flip failed"):
        viewer.run(1.0)

    assert binding.marked == []


def test_view_marks_rendered_manual_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = recommendation(assisted=False)
    binding = RecordingExitBinding(draft)
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)
    prepare_loop(monkeypatch, viewer, [], binding.timeline)

    viewer.run(1.0)

    assert binding.marked == [draft]


def test_view_does_not_mark_hidden_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = RecordingExitBinding(recommendation())
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)
    viewer._hud_enabled = False
    prepare_loop(monkeypatch, viewer, [], binding.timeline)

    viewer.run(1.0)

    assert binding.marked == []


def test_exit_keys_require_fresh_keydown_after_presented_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    draft = recommendation()
    binding = RecordingExitBinding(draft)
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)

    before_t0 = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, repeat=0)]
    )
    viewer._prepare_frame()
    viewer._draw(pygame.Surface((1280, 720)))
    viewer._after_display_flip()
    repeated = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, repeat=1)]
    )
    still_held = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, repeat=0)]
    )
    released = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYUP, key=pygame.K_c)]
    )
    fresh = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, repeat=0)]
    )

    assert before_t0.exit_confirm_requested is False
    assert repeated.exit_confirm_requested is False
    assert still_held.exit_confirm_requested is False
    assert released.exit_confirm_requested is False
    assert fresh.exit_confirm_requested is True


def test_nonrecommended_presentation_never_accepts_exit_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    draft = recommendation(assisted=False)
    binding = RecordingExitBinding(draft)
    viewer = ResearchDriverView(FakeWorld(), FakeResearchVehicle())
    viewer._feeds = [CameraFeed("front", FakeSensor())]
    viewer.set_exit_view_binding(binding)
    viewer._prepare_frame()
    viewer._draw(pygame.Surface((1280, 720)))
    viewer._after_display_flip()

    driver_input = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, repeat=0)]
    )

    assert binding.marked == [draft]
    assert driver_input.exit_confirm_requested is False


def test_scripted_driver_input_passes_existing_exit_flags_unchanged() -> None:
    scripted = DriverInput(exit_confirm_requested=True, exit_reject_requested=True)
    viewer = ResearchDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(driver_input_source=lambda: scripted),
    )

    collected = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, repeat=0)]
    )

    assert collected is scripted


def test_binding_hides_controls_after_decision_or_canonical_mode_change() -> None:
    draft = recommendation()

    class Coordinator:
        def __init__(self) -> None:
            self.status = ExitAssistanceStatus.PRESENTED

        def prepare_presentation(self, state: AutomationState) -> ExitPresentation:
            return draft

    class Observer:
        def __init__(self) -> None:
            self.presentation = draft
            self.coordinator = Coordinator()
            self.state = AutomationState(
                AutomationAvailability.AVAILABLE,
                DrivingControlMode.MANUAL,
            )

    observer = Observer()
    binding = ResearchExitViewBinding(observer, None, ExitKeys())

    mode_changed = binding.prepare_presentation()
    observer.state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )
    observer.coordinator.status = ExitAssistanceStatus.REJECTED
    decided = binding.prepare_presentation()

    assert mode_changed is not None
    assert mode_changed.navigation_text == draft.navigation_text
    assert mode_changed.recommendation is False
    assert decided is not None
    assert decided.navigation_text == draft.navigation_text
    assert decided.recommendation is False
