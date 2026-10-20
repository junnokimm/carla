from array import array
import pygame
import pytest

from src.scenario.hazard_warning_audio import (
    CUE_ID, DURATION_SECONDS, HazardWarningAudio, warning_pcm,
)
from src.scenario.turn_signal_audio import TurnSignalAudio
from src.scenario.vehicle_audio_assets import VOICE_COUNT
from src.scenario.vehicle_audio_playback import VehicleAudioPlayback
from tests.test_vehicle_audio_model import settle
from tests.vehicle_audio_fakes import FakeMixer, small_bank


@pytest.mark.parametrize("rate,channels", [(22050, 1), (44100, 2), (48000, 2)])
def test_pcm_has_three_audible_finite_pulses_and_silent_gaps(rate, channels):
    pcm = warning_pcm(rate, channels)
    assert pcm is warning_pcm(rate, channels)
    values = array("h", pcm)
    assert len(values) == round(rate * DURATION_SECONDS) * channels
    assert max(abs(v) for v in values) <= 6000
    for start, end in [(0, .16), (.26, .42), (.52, .68)]:
        assert max(abs(v) for v in values[round(start * rate) * channels:round(end * rate) * channels]) > 5900
    for start, end in [(.17, .25), (.43, .51)]:
        assert not any(values[round(start * rate) * channels:round(end * rate) * channels])
    if channels == 2:
        assert values[0::2] == values[1::2]


@pytest.mark.parametrize("fault", ["init", "sound", "play", "stop", "unsupported", "changed", "no_channel"])
def test_all_device_failures_are_recorded_without_raising(fault, monkeypatch):
    mixer = FakeMixer(None if fault == "init" else (22050, -16, 2),
                      fault if fault in ("init", "sound", "play", "stop") else None)
    if fault == "unsupported":
        mixer.config = (44100, -32, 2)
    cue = HazardWarningAudio(mixer=mixer)
    cue.prepare()
    if fault == "changed":
        mixer.config = None
    if fault == "no_channel":
        monkeypatch.setattr(cue.sound, "play", lambda: None)
    played = cue.play()
    cue.close()
    cue.close()
    report = cue.summary()
    assert report["session_action"] == played["session_action"] == "CONTINUE"
    assert not played["physical_audibility_verified"]
    if fault == "stop":
        assert played["play_request_accepted"] and report["errors"]
        mixer.now += DURATION_SECONDS + .01
        assert not any(c.get_busy() for c in mixer.channels)
    else:
        assert not played["play_request_accepted"]
    assert mixer.reserved == 0 and mixer.get_num_channels() == 8
    assert mixer.init_count <= 1


def test_prepare_once_play_does_not_synthesize_or_reopen_device(monkeypatch):
    mixer = FakeMixer(None)
    cue = HazardWarningAudio(mixer=mixer, clock_ns=iter([10, 20, 30, 40]).__next__)
    cue.prepare()
    cue.prepare()
    assert mixer.init_count == mixer.sound_count == 1
    def forbidden(*a):
        raise AssertionError("synthesis must not be in frame hook")
    monkeypatch.setattr("src.scenario.hazard_warning_audio.warning_pcm", forbidden)
    played = cue.play()
    assert played["request_started_perf_counter_ns"] == 10
    assert played["request_completed_perf_counter_ns"] == 20
    assert played["cue_id"] == CUE_ID and played["play_request_accepted"]
    mixer.config = None
    assert cue.play()["reason"] == "MIXER_CHANGED_OR_CLOSED"
    assert mixer.init_count == 1
    cue.close()


def test_warning_coexists_with_engine_and_click_without_stealing(monkeypatch):
    mixer = FakeMixer()
    monkeypatch.setattr(pygame, "mixer", mixer)
    engine = VehicleAudioPlayback(mixer, bank_provider=small_bank, wait=mixer.wait)
    click = TurnSignalAudio()
    cue = HazardWarningAudio()
    try:
        engine.prepare()
        engine.update(settle(speed_kmh=60, throttle=.8), 0)
        click.update(active=True, now=0)
        cue.prepare()
        assert cue.play()["play_request_accepted"]
        assert cue.sound.selected >= VOICE_COUNT
        assert cue.sound.selected != click._sound.selected
        sound = cue.sound
        cue.close()
        assert mixer.reserved == VOICE_COUNT
        assert mixer.channels[click._sound.selected].get_busy()
        assert any(c.get_busy() for c in mixer.channels[:VOICE_COUNT])
        assert all(c.stops == 0 for c in mixer.channels if c.sound is not sound)
    finally:
        cue.close()
        click.close()
        engine.close(fade=False)


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize("fault", [None, "sound", "play", "stop", "logging", "render"])
def test_scene_first_visible_warning_once_with_nonfatal_faults(tmp_path, monkeypatch, mode, fault):
    from tests.test_clear_road_hazard import setup, expose
    from tests.test_m2_hazard_v1 import tick

    mixer = FakeMixer(fail=fault if fault in ("sound", "play", "stop") else None)
    monkeypatch.setattr(pygame, "mixer", mixer)
    scene, world, clock, events, records, _ = setup(tmp_path, mode)
    expose(scene, world)
    try:
        scene.after_warning_flip(1, 2)
        assert scene.warning_audio.attempts == 0
        def fail(*args, **kwargs):
            raise RuntimeError("injected optional warning failure")
        if fault == "logging":
            monkeypatch.setattr(scene.services, "emit", fail)
        pygame.font.init()
        with monkeypatch.context() as patch:
            if fault == "render":
                patch.setattr(pygame.font, "SysFont", fail)
            scene.draw_warning(pygame.Surface((1280, 720)))
            scene.after_warning_flip(123, 456)
        if fault == "render":
            assert scene.warning_audio.attempts == 0
            scene.draw_warning(pygame.Surface((1280, 720)))
            scene.after_warning_flip(789, 999)
        for _ in range(10):
            scene.draw_warning(pygame.Surface((1280, 720)))
            scene.after_warning_flip(1000, 2000)
        assert scene.warning_audio.attempts == 1
        assert scene.warning_record["audio"]["cue_id"] == CUE_ID
        assert scene.warning_record["audio"]["play_request_accepted"] is (fault not in ("sound", "play"))
        assert scene.warning_record["takeover_request"] is (mode == "NOA_ACTIVE")
        assert not scene.warning_record["changes_control_authority"]
        assert scene.scene_failure is None
        assert scene.session.bundle.automation_runtime.state.control_mode.value == mode
        assert world.vehicle.applied_controls == world.vehicle.transform_commands == []
        assert world.vehicle.physics_commands == []
        # A device fault never converts a valid physical hazard to INVALID.
        if fault == "logging":
            monkeypatch.undo()
        tick(scene, world, 780 - clock(), y=-3.5)
    finally:
        scene.finalize("M2_END")
    assert records[-1]["warning_audio"]["attempts"] == 1
    assert records[-1]["warning_audio"]["closed"]
    assert records[-1]["warning"]["audio"]["cue_id"] == CUE_ID
    assert records[-1]["m2_active_elapsed_s"] == pytest.approx(780)
    assert scene.scene_failure is None


def test_reaction_report_keeps_audio_evidence_but_does_not_move_reaction_origin():
    from src.p8.collection_hazard import hazard_metrics
    from tests.test_p8_collection import hazard_events
    events = hazard_events()
    events.append(("m2_hazard_warning_displayed", 10.05, {
        "collision_warning": True, "takeover_request": True,
        "flip_completed_monotonic_ns": 10_050_000_000,
        "audio": {"cue_id": CUE_ID, "status": "FAILED", "play_request_accepted": False},
    }))
    report = hazard_metrics(events, 0)
    assert report["reaction_time_s"] == pytest.approx(1.0)
    assert report["warning"]["audio"]["status"] == "FAILED"


def test_no_repeat_after_takeover_and_no_warning_for_unexposed_scene(tmp_path, monkeypatch):
    from tests.test_clear_road_hazard import setup, expose
    mixer = FakeMixer()
    monkeypatch.setattr(pygame, "mixer", mixer)
    scene, world, *_ = setup(tmp_path)
    pygame.font.init()
    canvas = pygame.Surface((1280, 720))
    scene.draw_warning(canvas)
    scene.after_warning_flip(1, 2)
    assert scene.warning_audio.attempts == 0
    expose(scene, world)
    scene.draw_warning(canvas)
    scene.after_warning_flip(3, 4)
    scene.session.bundle.automation_runtime.state.control_mode.value = "MANUAL"
    scene.draw_warning(canvas)
    scene.after_warning_flip(5, 6)
    assert scene.warning_audio.attempts == 1
    assert scene.warning_record["takeover_request"]
    assert scene.warning_record["flip_completed_perf_counter_ns"] == 4
    scene.finalize("M2_END")
