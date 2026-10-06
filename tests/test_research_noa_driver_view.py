from __future__ import annotations

from collections import defaultdict

import pygame

from src.experiment.automation import DrivingControlMode
from src.scenario.driver_view import DriverView
from src.scenario.research_noa import (
    ResearchDriverView,
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.vehicle.driving_mode import DrivingMode
from tests.research_noa_fakes import (
    FakeClient,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)
from tests.test_driver_view import FakeWorld
from tests.test_driver_view_runtime import FakeFrameClock, FakeTurnSignalAudio
from tests.test_noa_runtime import make_control_config


class RealDriverViewFactory:
    def __init__(self) -> None:
        self.camera_world = FakeWorld()
        self.created: list[ResearchDriverView] = []

    def __call__(
        self,
        world: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: ResearchDriverViewConfig,
    ) -> ResearchDriverView:
        assert world.vehicle is hero
        viewer = ResearchDriverView(self.camera_world, hero, config)
        self.created.append(viewer)
        return viewer


class RealLiveDriverViewFactory:
    def __init__(self) -> None:
        self.camera_world = FakeWorld()
        self.created: list[ResearchLiveDriverView] = []

    def __call__(
        self,
        world: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: ResearchDriverViewConfig,
    ) -> ResearchLiveDriverView:
        assert world.vehicle is hero
        viewer = ResearchLiveDriverView(self.camera_world, hero, config)
        self.created.append(viewer)
        return viewer


def test_runner_real_driver_view_manual_frames_do_not_inject_control(
    monkeypatch,
) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    clock = FakeFrameClock()
    times = iter((0.0, 0.0, 0.1, 0.2, 1.0))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    monkeypatch.setattr(
        driver_view.pygame.key,
        "get_pressed",
        lambda: defaultdict(bool),
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = RealDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        assert [feed.role for feed in viewer.feeds] == ["front", "left", "right"]
        assert len(viewer_factory.camera_world.spawn_calls) == 3
        monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
        monkeypatch.setattr(viewer, "_draw", lambda screen: None)

        session.run()

        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert session.bundle.control_backend.active is False
        assert vehicle.applied_controls == []

    assert vehicle.autopilot_calls == [False, False]
    assert vehicle.destroy_count == 1
    assert [
        (sensor.stop_count, sensor.destroy_count)
        for sensor in (viewer_factory.camera_world.sensors)
    ] == [(1, 1), (1, 1), (1, 1)]
    assert clock.tick_rates == [driver_view.FRAME_RATE] * 3
    assert audio.close_count >= 1


def test_live_driver_view_allows_manual_keyboard_control_but_ignores_p_key(
    monkeypatch,
) -> None:
    hero = FakeResearchVehicle()
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        hero,
        ResearchDriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )
    keys: defaultdict[int, bool] = defaultdict(bool)
    keys[pygame.K_w] = True
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    viewer._apply_manual_control()

    assert viewer.driving_mode is DrivingMode.MANUAL
    assert hero.autopilot_calls == [False]
    assert len(hero.applied_controls) == 1
    assert hero.applied_controls[0].throttle == 1.0


def test_runner_front_camera_only_cleans_one_camera_and_hero(monkeypatch) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = RealDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_control_config(),
            front_camera_only=True,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session():
        viewer = viewer_factory.created[0]
        assert [feed.role for feed in viewer.feeds] == ["front"]
        assert len(viewer_factory.camera_world.spawn_calls) == 1
        spawn = viewer_factory.camera_world.spawn_calls[0]
        assert spawn.blueprint.identifier == "sensor.camera.rgb"
        assert spawn.attached_to is vehicle
        assert spawn.attributes == {
            "image_size_x": "1280",
            "image_size_y": "720",
            "fov": "100.0",
            "exposure_compensation": "0.5",
        }
        assert "sensor_tick" not in spawn.attributes
        viewer._draw(pygame.Surface((1280, 720)))
        assert [feed.role for feed in viewer.feeds] == ["front"]

    assert [
        (sensor.stop_count, sensor.destroy_count)
        for sensor in viewer_factory.camera_world.sensors
    ] == [(1, 1)]
    assert vehicle.destroy_count == 1


def test_live_runner_real_driver_view_has_one_writer_per_active_frame(
    monkeypatch,
) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    clock = FakeFrameClock()
    diagnostics_clock = FakeMonotonicClock()
    times = iter((0.0, 0.0, 0.1, 0.2, 1.0))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", lambda: clock)
    monkeypatch.setattr(
        driver_view.pygame,
        "quit",
        lambda: diagnostics_clock.advance(10.0),
    )
    monkeypatch.setattr(
        driver_view.pygame.key,
        "get_pressed",
        lambda: defaultdict(bool),
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = RealLiveDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        assert session.live_scheduler is not None
        diagnostics = ResearchNoADiagnostics(diagnostics_clock)
        session.live_scheduler.diagnostics = diagnostics
        session.live_scheduler._recording_backend._diagnostics = diagnostics
        monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
        monkeypatch.setattr(
            DriverView,
            "_draw",
            lambda self, screen: diagnostics_clock.advance(0.01),
        )

        report = session.run()

        assert report.scheduler_updates == 3
        assert report.control_frames == 3
        assert report.diagnostics.driver_loop_iterations == 3
        assert report.diagnostics.driver_loop_mean_ms == 10.0
        assert report.diagnostics.driver_loop_max_ms == 10.0
        assert report.diagnostics.render_mean_ms == 10.0
        assert world.get_snapshot_count == 2
        assert world.wait_for_tick_calls == []
        assert len(vehicle.applied_controls) == 4
        assert vehicle.applied_controls[-1].brake == 0.5
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )

    assert vehicle.destroy_count == 1
    assert clock.tick_rates == [driver_view.FRAME_RATE] * 3
    assert audio.close_count >= 1
