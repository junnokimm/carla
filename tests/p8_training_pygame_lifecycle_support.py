from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import carla
import pygame
import pytest

from src.experiment.automation_interaction import AutomationInteractionConfig
from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import OperatorRuntimeConfig, RegistrationRequest
from src.p8.operator_ui_types import OperatorUiConfig
from src.scenario.research_noa_config import ResearchNoARunConfig
from src.scenario.research_noa_log_setup import OpenResearchLog
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)
from tests.test_p8_operator_backend import StrictClock


@dataclass(slots=True)
class LifecycleEvidence:
    calls: list[str] = field(default_factory=list)
    viewer_close_counts: list[int] = field(default_factory=list)
    sensors: list[LifecycleSensor] = field(default_factory=list)
    heroes: list[LifecycleHero] = field(default_factory=list)
    borrowed_logger_ids: list[int] = field(default_factory=list)
    borrowed_clock_ids: list[int] = field(default_factory=list)
    configured_durations: list[float] = field(default_factory=list)
    restored_at: float | None = None
    quit_handled_at: float | None = None
    restored_video_driver: str | None = None
    restored_surface: bool = False


class LifecycleSensor:
    def __init__(self, evidence: LifecycleEvidence) -> None:
        self._evidence = evidence
        self.stop_count = 0
        self.destroy_count = 0
        self.callback: Callable[[carla.Image], None] | None = None

    def listen(self, callback: Callable[[carla.Image], None]) -> None:
        self.callback = callback
        self._evidence.calls.append("sensor.listen")

    def stop(self) -> None:
        self.stop_count += 1
        self._evidence.calls.append("sensor.stop")

    def destroy(self) -> bool:
        self.destroy_count += 1
        self._evidence.calls.append("sensor.destroy")
        return True


class LifecycleHero(FakeResearchVehicle):
    def __init__(self, evidence: LifecycleEvidence) -> None:
        super().__init__()
        self._evidence = evidence

    def destroy(self) -> bool:
        self._evidence.calls.append("hero.destroy")
        return super().destroy()


class LifecycleWorld(FakeResearchWorld):
    def __init__(self, hero: LifecycleHero, evidence: LifecycleEvidence) -> None:
        super().__init__(hero)
        self._evidence = evidence

    def spawn_actor(self, blueprint, transform, attach_to=None):
        self.spawn_calls.append((blueprint, transform))
        if attach_to is None:
            return self.vehicle
        sensor = LifecycleSensor(self._evidence)
        self._evidence.sensors.append(sensor)
        return sensor


def build_controller(
    root: Path,
    evidence: LifecycleEvidence,
    event: pygame.event.Event,
) -> OperatorController:
    clock = StrictClock()

    def build(
        config: ResearchNoARunConfig,
        borrowed_log: OpenResearchLog,
    ) -> ResearchNoARunner:
        hero = LifecycleHero(evidence)
        world = LifecycleWorld(hero, evidence)
        evidence.heroes.append(hero)
        evidence.borrowed_logger_ids.append(id(borrowed_log.logger))
        evidence.borrowed_clock_ids.append(id(borrowed_log.session_clock))
        evidence.configured_durations.append(config.duration)

        class EventPostingView(ResearchLiveDriverView):
            def __init__(
                self,
                world_arg: LifecycleWorld,
                hero_arg: LifecycleHero,
                view_config: ResearchDriverViewConfig,
            ) -> None:
                super().__init__(
                    world_arg,
                    hero_arg,
                    replace(view_config, front_camera_only=True),
                )
                self._posted = False
                self._close_count = 0

            def _begin_iteration(self) -> None:
                super()._begin_iteration()
                if self._posted:
                    return
                self._posted = True
                evidence.calls.append(f"driver.event:{event.type}")
                pygame.event.post(event)

            def close(self) -> None:
                self._close_count += 1
                evidence.viewer_close_counts.append(self._close_count)
                evidence.calls.append("viewer.close")
                super().close()

        def viewer_factory(
            world_arg: LifecycleWorld,
            hero_arg: LifecycleHero,
            view_config: ResearchDriverViewConfig,
        ) -> EventPostingView:
            return EventPostingView(world_arg, hero_arg, view_config)

        return ResearchNoARunner(
            config,
            client=FakeClient(world),
            viewer_factory=viewer_factory,
            borrowed_log=borrowed_log,
            monotonic_ns=clock.monotonic_ns,
            utc_ns=clock.utc_ns,
        )

    return OperatorController(
        root,
        mode=Mode.DEV_REHEARSAL,
        runtime=OperatorRuntimeConfig(
            spawn_index=0,
            control_config=replace(
                make_live_control_config(),
                longitudinal=replace(
                    make_live_control_config().longitudinal,
                    target_speed_kmh=None,
                ),
            ),
            speed_policy=None,
            interaction_policy=AutomationInteractionConfig(
                0.2,
                0.1,
                0.05,
                True,
                True,
            ),
            runner_builder=build,
        ),
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )


def ui_config() -> OperatorUiConfig:
    return OperatorUiConfig(
        registration=RegistrationRequest(
            participant_id="R-0042",
            run_id="pygame-lifecycle",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=Module2Condition.MANUAL,
        ),
        training_duration_s=600.0,
        drive_duration_s=1.0,
        window_size=(640, 360),
    )


def prepare_sdl(monkeypatch: pytest.MonkeyPatch) -> None:
    pygame.quit()
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.delenv("SDL_VIDEODRIVER", raising=False)
