from __future__ import annotations

from collections import defaultdict
from math import isnan, nan

import pygame
import pytest

from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import ExperimentModule, Module1Condition
from src.scenario.driver_view import DriverView, DriverViewConfig
from src.scenario.research_noa import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from src.vehicle.driving_mode import DrivingMode
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_driver_view_runtime import FakeFrameClock, FakeTurnSignalAudio
from tests.test_noa_runtime import FakeVelocity


class SequencedManualSpeedVehicle(FakeResearchVehicle):
    def __init__(self, speed_samples_kmh: tuple[float, ...]) -> None:
        super().__init__()
        self._speed_samples = iter(speed_samples_kmh)

    def get_velocity(self) -> FakeVelocity:
        speed_mps = next(self._speed_samples) / 3.6
        return FakeVelocity(speed_mps, 0.0, 0.0)


class ManualApplyReachedError(RuntimeError):
    pass


class ManualSafetyViewer(ResearchLiveDriverView):
    def __init__(
        self,
        world: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: ResearchDriverViewConfig,
    ) -> None:
        super().__init__(world, hero, config)
        self.close_count = 0

    def attach(self) -> None:
        return

    def close(self) -> None:
        self.close_count += 1

    def _draw(self, screen: pygame.Surface) -> None:
        return

    def _update_turn_signal_audio(self) -> None:
        raise ManualApplyReachedError


class ManualSafetyViewerFactory:
    def __init__(self) -> None:
        self.created: list[ManualSafetyViewer] = []

    def __call__(
        self,
        world: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: ResearchDriverViewConfig,
    ) -> ManualSafetyViewer:
        viewer = ManualSafetyViewer(world, hero, config)
        self.created.append(viewer)
        return viewer


def test_opt_in_research_manual_control_clamps_and_prioritizes_brake() -> None:
    hero = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        hero,
        ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            manual_control_limits=ResearchManualControlLimits(0.4, 0.5, 0.15),
        ),
    )
    viewer._pending_driver_input = DriverInput(
        throttle=1.0,
        brake=1.0,
        steering=1.0,
        steering_engaged=True,
    )

    viewer._apply_manual_control()

    assert len(hero.applied_controls) == 1
    control = hero.applied_controls[0]
    assert control.throttle == 0.0
    assert control.brake == pytest.approx(0.5)
    assert control.steer == pytest.approx(0.15)


def test_legacy_driver_view_manual_control_remains_full_scale(monkeypatch) -> None:
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_w] = True
    keys[pygame.K_d] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    hero = FakeHero()
    viewer = DriverView(
        FakeWorld(),
        hero,
        DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )

    viewer._apply_manual_control()

    assert hero.control_calls[0].throttle == 1.0


def test_research_noa_key_is_configurable_and_defaults_to_n(monkeypatch) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(noa_toggle_key=pygame.K_b),
    )

    configured = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_b)]
    )
    old_default = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n)]
    )

    assert configured.activation_requested is True
    assert configured.deactivation_requested is True
    assert old_default.activation_requested is False
    assert old_default.deactivation_requested is False


def test_manual_end_state_still_receives_one_bounded_shutdown_brake() -> None:
    inputs = iter(
        (
            DriverInput(activation_requested=True),
            DriverInput(brake=0.1),
        )
    )
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            automation_interaction=ResearchAutomationInteractionConfig(
                ExperimentModule.MODULE_1,
                Module1Condition.NO_SURT,
                AutomationInteractionConfig(0.2, 0.1, 0.05, True),
            ),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        driver_input_source=inputs.__next__,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None, lambda: None]

        report = session.run()

        assert report.control_frames == 1
        assert session.bundle.automation_runtime.state.control_mode.name == "MANUAL"
        shutdown = vehicle.applied_controls[-1]
        assert shutdown.throttle == 0.0
        assert shutdown.brake == pytest.approx(0.5)
        assert shutdown.steer == 0.0


@pytest.mark.parametrize("unsafe_speed_kmh", [21.0, nan])
def test_manual_speed_guard_aborts_before_actual_viewer_applies_input(
    unsafe_speed_kmh: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.scenario import driver_view

    clock = FakeFrameClock()
    times = iter((0.0, 0.0, 0.0))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", FakeTurnSignalAudio)
    monkeypatch.setattr(driver_view.time, "monotonic", times.__next__)
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    vehicle = SequencedManualSpeedVehicle((0.0, unsafe_speed_kmh))
    viewer_factory = ManualSafetyViewerFactory()
    controls_before_scheduler: list[int] = []

    def unsafe_manual_input() -> DriverInput:
        controls_before_scheduler.append(len(vehicle.applied_controls))
        return DriverInput(throttle=1.0)

    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            automation_interaction=ResearchAutomationInteractionConfig(
                ExperimentModule.MODULE_1,
                Module1Condition.NO_SURT,
                AutomationInteractionConfig(0.2, 0.1, 0.05, True),
            ),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        driver_input_source=unsafe_manual_input,
    )

    with runner.session() as session:
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value.actual_speed_kmh == unsafe_speed_kmh or (
            isnan(caught.value.actual_speed_kmh) and isnan(unsafe_speed_kmh)
        )
        assert controls_before_scheduler == [0]
        assert len(vehicle.applied_controls) == controls_before_scheduler[0] + 1
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
        assert session.live_scheduler is not None
        assert session.live_scheduler.update_count == 1
        assert session.live_scheduler.commands == ()
        assert session.bundle.automation_runtime.state.control_mode.name == "MANUAL"

    assert viewer_factory.created[0].close_count == 1
    assert vehicle.destroy_count == 1
