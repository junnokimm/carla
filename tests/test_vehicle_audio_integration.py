import json
from dataclasses import replace
from types import SimpleNamespace

import pygame
import pytest

from src.scenario import driver_view, research_noa_view
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
)
from src.scenario.research_performance_summary import build_performance_summary
from src.scenario.vehicle_audio_playback import VehicleAudioPlayback
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_vehicle_audio_model import state
from tests.vehicle_audio_fakes import FakeMixer, small_bank


@pytest.mark.parametrize("ending", ["duration", "f8", "escape", "window", "error"])
def test_actual_view_loop_audio_cleanup_before_pygame_quit(monkeypatch, ending):
    mixer = FakeMixer()
    events = []
    summaries = []
    monkeypatch.setattr(pygame, "mixer", mixer)
    monkeypatch.setattr(
        research_noa_view,
        "VehicleAudioPlayback",
        lambda backend: VehicleAudioPlayback(
            backend, bank_provider=small_bank, wait=mixer.wait
        ),
    )
    monkeypatch.setattr(driver_view, "DriverHudRenderer", lambda *a, **kw: object())
    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: None)
    monkeypatch.setattr(
        driver_view,
        "DriverViewPresentation",
        lambda size, screen: SimpleNamespace(
            canvas=object(), handle_events=lambda e: None, present=lambda: None
        ),
    )
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: mixer.now)
    monkeypatch.setattr(
        pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda fps: mixer.wait(0.1))
    )
    monkeypatch.setattr(
        driver_view.DriverView, "_update_turn_signal_audio", lambda self: None
    )

    def quit_pygame():
        assert not any(c.get_busy() for c in mixer.channels)
        assert mixer.reserved == 0
        events.append("pygame.quit")

    monkeypatch.setattr(pygame, "quit", quit_pygame)

    def poll():
        if mixer.now < 0.4 or ending in ("duration", "error"):
            return []
        if ending == "window":
            return [SimpleNamespace(type=pygame.QUIT)]
        return [
            SimpleNamespace(
                type=pygame.KEYDOWN,
                key=pygame.K_F8 if ending == "f8" else pygame.K_ESCAPE,
            )
        ]

    monkeypatch.setattr(pygame.event, "get", poll)
    view = ResearchLiveDriverView(
        FakeWorld(),
        FakeHero(),
        ResearchDriverViewConfig(
            training_early_finish=True,
            vehicle_audio_source=lambda: state(mixer.now, speed_kmh=40, throttle=0.7),
            vehicle_audio_status_sink=summaries.append,
        ),
    )
    view._vehicle_audio.clock = lambda: mixer.now
    view._tsxw_input = SimpleNamespace(close=lambda: events.append("input.close"))
    monkeypatch.setattr(view, "_handle_mode_events", lambda e: None)
    monkeypatch.setattr(view, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(view, "_prepare_frame", lambda: None)
    monkeypatch.setattr(view, "_after_display_flip", lambda: None)

    def draw(screen):
        if ending == "error" and mixer.now >= 0.4:
            mixer.fail = "stop"  # cleanup failure must not replace this error
            raise ValueError("primary draw failure")

    monkeypatch.setattr(view, "_draw", draw)
    view.attach()  # Fake sensors, Fake mixer. Production preparation wiring.
    diagnostics = ResearchNoADiagnostics(lambda: mixer.now)
    if ending == "error":
        # Backend stop failures are recorded; finite grains still expire independently.
        monkeypatch.setattr(pygame, "quit", lambda: events.append("pygame.quit"))
        with pytest.raises(ValueError, match="primary draw failure"):
            view.run(0.6, diagnostics=diagnostics)
    else:
        exited = view.run(0.6, diagnostics=diagnostics)
        assert exited is (ending in ("escape", "window"))
        assert view.training_completed_early is (ending == "f8")
    view.close()
    assert events == ["input.close", "pygame.quit"]
    assert view._vehicle_audio.playback.closed
    assert len(summaries) == 1
    summary = json.loads(summaries[0])
    assert summary["grain_launches"] >= 2 and summary["rpm_source"] == "estimated_rpm"
    if ending == "error":
        assert summary["cleanup_errors"]
    performance = json.loads(
        build_performance_summary(
            diagnostics=diagnostics,
            world=None,
            wall_elapsed_seconds=0.6,
            scheduler_updates=0,
            camera_diagnostics_json=None,
            error=None,
        )
    )
    assert performance["operations"]["vehicle_audio_update"]["count"] >= 3
    assert "snapshot_getter" in performance["operations"]


@pytest.mark.parametrize("audio_enabled", [False, True])
def test_operator_audio_default_off_and_explicit_runner_opt_in(
    tmp_path, monkeypatch, audio_enabled
):
    from src.p8.model import Stage
    from src.p8.operator_model import DevOutcome, TrainingRun
    from tests import test_p8_operator_backend as backend
    from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev

    original_runner = backend.ResearchNoARunner

    def runner(config, **kwargs):
        assert config.vehicle_audio is False  # Actual P8 builder, all three Stages.
        return original_runner(replace(config, vehicle_audio=audio_enabled), **kwargs)

    monkeypatch.setattr(backend, "ResearchNoARunner", runner)
    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    try:
        advance_to(controller, Stage.TRAINING)
        controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
        advance_to(controller, Stage.MODULE_1_RUNNING)
        controller.execute_current_module(1.0)
        controller.approve_dev_outcome(DevOutcome.PARTIAL, "offline audio integration")
        controller.advance()
        controller.advance()
        clock.advance(120)
        controller.advance()
        controller.advance()
        controller.execute_current_module(1.0)
        assert len(evidence.viewers) == 3
        if audio_enabled:
            samples = [v.config.vehicle_audio_source() for v in evidence.viewers]
            assert len({s.stage_id for s in samples}) == 3
            assert all(s.measured_rpm is None for s in samples)
            assert all(
                v.config.vehicle_audio_status_sink is not None for v in evidence.viewers
            )
        else:
            for viewer in evidence.viewers:
                assert viewer.config.vehicle_audio_source is None
                assert viewer.config.vehicle_audio_status_sink is None
                assert_disabled_audio_keeps_clicks(monkeypatch, viewer.config)
    finally:
        controller.abort("OFFLINE_TEST_COMPLETE")


def assert_disabled_audio_keeps_clicks(monkeypatch, config):
    import carla

    from src.scenario import vehicle_audio_assets
    from tests.research_noa_fakes import FakeResearchVehicle

    def forbidden(*args, **kwargs):
        pytest.fail(
            "disabled vehicle audio must not construct/prepare/reserve/synthesize"
        )

    calls = []
    mixer = SimpleNamespace(
        get_init=lambda: None,
        init=lambda **kwargs: calls.append("mixer.init"),
        Sound=lambda **kwargs: SimpleNamespace(
            play=lambda: calls.append("click.play"),
            stop=lambda: calls.append("click.stop"),
        ),
        set_reserved=forbidden,
        set_num_channels=forbidden,
        Channel=forbidden,
    )
    with monkeypatch.context() as patch:
        patch.setattr(research_noa_view, "VehicleAudioRuntime", forbidden)
        patch.setattr(research_noa_view, "VehicleAudioPlayback", forbidden)
        patch.setattr(VehicleAudioPlayback, "prepare", forbidden)
        patch.setattr(vehicle_audio_assets, "procedural_bank", forbidden)
        patch.setattr(pygame, "mixer", mixer)
        hero = FakeResearchVehicle()
        view = ResearchLiveDriverView(FakeWorld(), hero, config)
        assert view._vehicle_audio is None
        view.attach()
        hero.set_light_state(carla.VehicleLightState.RightBlinker)
        view._update_turn_signal_audio()
        hero.set_light_state(carla.VehicleLightState.NONE)
        view._update_turn_signal_audio()
        view._close_input()
        view.close()
        assert calls == ["mixer.init", "click.play", "click.stop", "click.stop"]
