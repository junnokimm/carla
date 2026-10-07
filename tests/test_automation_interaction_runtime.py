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
from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    AutomationInteractionController,
    AutomationInteractionEvent,
    DeactivationReason,
    DriverInput,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.context import ExperimentModule, Module1Condition
from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.scenario.driver_view import DriverView, DriverViewConfig
from src.scenario.research_noa import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext
from src.vehicle.carla_noa_simulation_control import CarlaNoAControlStep
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


def interaction_config() -> ResearchAutomationInteractionConfig:
    return ResearchAutomationInteractionConfig(
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.NO_SURT,
        policy=AutomationInteractionConfig(0.2, 0.1, 0.05, True),
        initial_stage="m1_fixture",
    )


def test_session_script_uses_real_runtime_input_path_and_single_writer() -> None:
    inputs = iter(
        (
            DriverInput(activation_requested=True, stage="activate"),
            DriverInput(steering=0.8, steering_engaged=True, stage="steer"),
            DriverInput(brake=0.1, stage="brake"),
            DriverInput(activation_requested=True, stage="reactivate"),
        )
    )
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    traced_events: list[AutomationInteractionEvent] = []
    traced_steps: list[CarlaNoAControlStep] = []
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            automation_interaction=interaction_config(),
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        driver_input_source=inputs.__next__,
        automation_event_sink=traced_events.append,
        control_trace=traced_steps.append,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None] * 4

        report = session.run()

        assert report.control_frames == 3
        assert report.scheduler_updates == 4
        assert len(session.live_scheduler.commands) == 3
        assert session.live_scheduler.commands[1].steering == pytest.approx(0.15)
        assert traced_steps[-1].delta_seconds == 0.0
        assert traced_steps[-1].integral_effort == 0.0
        assert [event.stage for event in session.interaction_events] == [
            "m1_fixture",
            "activate",
            "activate",
            "brake",
            "brake",
            "brake",
            "reactivate",
            "reactivate",
            "reactivate",
        ]
        assert traced_events == session.interaction_events
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)

    assert vehicle.destroy_count == 1
    assert viewer_factory.created[0].close_count == 1
    assert vehicle.autopilot_calls == [False, False, False, False, False]


def test_activation_applies_engaged_driver_steering_on_first_backend_step() -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            automation_interaction=interaction_config(),
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        driver_input_source=lambda: DriverInput(
            activation_requested=True,
            steering=-0.8,
            steering_engaged=True,
        ),
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]

        report = session.run()

        assert report.control_frames == 1
        assert report.commands[0].steering == pytest.approx(-0.15)
        assert vehicle.applied_controls[0].steer == pytest.approx(-0.15)


def test_n_key_maps_to_research_noa_request_without_using_p_key(monkeypatch) -> None:
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    viewer = DriverView(
        FakeWorld(),
        ViewHero(),
        DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )

    driver_input = viewer._collect_driver_input(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n)]
    )

    assert driver_input.activation_requested is True
    assert driver_input.deactivation_requested is True


class RuntimeBackend:
    def __init__(self) -> None:
        self.steering: float | None = None

    def enter_manual_control(self) -> None:
        return

    def enter_noa_control(self) -> None:
        return

    def set_driver_steering(self, steering: float | None) -> None:
        self.steering = steering


@dataclass(frozen=True, slots=True)
class ViewGeometry:
    def observe_with_context(self, vehicle: ViewHero) -> CarlaLaneGeometryContext:
        return CarlaLaneGeometryContext(
            LaneGeometryObservation(0.0, 0.0),
            1,
            0,
            -2,
            3.5,
            PlanarPose(0.0, 0.0, 0.0),
            PlanarPose(0.0, 0.0, 0.0),
        )


@dataclass(frozen=True, slots=True)
class ViewExtent:
    x: float = 2.0
    y: float = 0.8


@dataclass(frozen=True, slots=True)
class ViewRotation:
    yaw: float = 0.0


@dataclass(frozen=True, slots=True)
class ViewBoundingBox:
    extent: ViewExtent = ViewExtent()
    location: ViewExtent = ViewExtent(0.0, 0.0)
    rotation: ViewRotation = ViewRotation()


class ViewHero(FakeHero):
    def __init__(self) -> None:
        super().__init__()
        self.bounding_box = ViewBoundingBox()


class ModeRecordingScheduler:
    def __init__(self, runtime: AutomationRuntimeController) -> None:
        self.runtime = runtime
        self.modes: list[DrivingControlMode] = []

    def update(self) -> bool:
        self.modes.append(self.runtime.state.control_mode)
        return self.runtime.state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_brake_disengagement_applies_manual_brake_in_same_driver_iteration(
    monkeypatch,
) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    clock = FakeFrameClock()
    times = iter((0.0, 0.0, 1.0))
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_s] = True
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(driver_view.pygame.key, "get_pressed", lambda: keys)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    hero = ViewHero()
    backend = RuntimeBackend()
    runtime = AutomationRuntimeController(
        AutomationState(
            AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
        ),
        backend,
    )
    events: list[AutomationInteractionEvent] = []
    interaction = AutomationInteractionController(
        runtime=runtime,
        geometry=ViewGeometry(),
        vehicle=hero,
        steering_target=backend,
        config=AutomationInteractionConfig(0.2, 0.1, 0.05, True),
        event_sink=events.append,
    )
    scheduler = ModeRecordingScheduler(runtime)
    viewer = DriverView(
        FakeWorld(),
        hero,
        DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(viewer, "_draw", lambda screen: None)

    viewer.run(1.0, scheduler=scheduler, input_observer=interaction)

    assert scheduler.modes == [DrivingControlMode.MANUAL]
    assert len(hero.control_calls) == 1
    assert hero.control_calls[0].brake == 1.0
    assert events[-2].deactivation_reason is DeactivationReason.DRIVER_BRAKE
