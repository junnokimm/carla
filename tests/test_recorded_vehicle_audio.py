import hashlib
import json
import math
import shutil
import wave
from array import array
from dataclasses import replace

import pygame
import pytest

from src.scenario import hazard_warning_audio
from src.scenario.hazard_warning_audio import HazardWarningAudio, warning_pcm
from src.scenario.recorded_vehicle_audio import (
    WARNING_DUCK_GAIN,
    RecordedVehicleAudioPlayback,
    RecordedVehicleAudioRuntime,
)
from src.scenario.recorded_vehicle_audio_assets import ASSET_DIRECTORY, recorded_bank
from src.scenario.recorded_vehicle_audio_model import (
    MASTER_GAIN,
    RecordedEngineSoundModel,
)
from src.scenario.turn_signal_audio import TurnSignalAudio
from tests.recorded_vehicle_audio_fakes import RecordedFakeMixer, recorded_test_bank
from tests.test_vehicle_audio_model import state


@pytest.fixture
def player():
    mixer = RecordedFakeMixer()
    playback = RecordedVehicleAudioPlayback(
        mixer,
        bank_provider=recorded_test_bank,
        clock=lambda: mixer.now,
        wait=mixer.wait,
    )
    playback.prepare()
    assert playback.available
    yield playback, mixer
    playback.close(fade=False)


def settled(**kwargs):
    model = RecordedEngineSoundModel()
    for i in range(241):
        parameters = model.update(state(i / 60, **kwargs), i / 60)
    return parameters


def test_idle_accelerating_cruise_and_braking_mix_differ_without_control_mutation():
    idle = settled()
    assert idle.gains[0] == pytest.approx(MASTER_GAIN, abs=0.0001)
    assert idle.gains[1:] == (0, 0)
    cruise = settled(speed_kmh=90, throttle=0.2)
    assert cruise.gains[2] > 0.48 and cruise.gains[0] == 0
    assert settled(speed_kmh=25, throttle=0.2).gains[2] < cruise.gains[2] * 0.6
    model = RecordedEngineSoundModel()
    for i in range(241):
        t = i / 60
        observation = state(t, speed_kmh=20 + 5 * t, throttle=0.8)
        acceleration = model.update(observation, t)
        assert (
            observation.throttle == 0.8
        )  # observation is immutable; never VehicleControl
        assert sum(acceleration.gains) <= MASTER_GAIN + 1e-9
    assert acceleration.gains[1] > 0.5
    braking = settled(speed_kmh=70, throttle=0.8, brake=1)
    assert braking.gains[1] == 0 and braking.gains[2] > 0.43
    assert settled(reverse=True, gear=-1, speed_kmh=10, throttle=0.3).fresh


def test_mode_change_does_not_reset_sound_and_abrupt_input_is_smoothed():
    model = RecordedEngineSoundModel()
    for i in range(121):
        before = model.update(state(i / 60), i / 60)
    after = model.update(state(2.01, mode="NOA_ACTIVE"), 2.01)
    assert after.gains[0] >= before.gains[0]
    change = model.update(state(2.02, speed_kmh=100, throttle=1), 2.02)
    assert max(abs(a - b) for a, b in zip(after.gains, change.gains)) < 0.025


@pytest.mark.parametrize(
    "bad",
    [
        None,
        state(speed_kmh=float("nan")),
        state(throttle=float("inf")),
        state(brake=float("nan")),
    ],
)
def test_invalid_stale_or_missing_observation_is_silent_not_exception(bad):
    model = RecordedEngineSoundModel()
    result = model.update(bad, 1.0)
    assert not result.fresh and result.gains == (0, 0, 0)


def test_preloaded_contiguous_queue_no_runtime_asset_loading_or_channel_allocation(
    player,
):
    p, mixer = player
    counts = mixer.sound_count, mixer.channel_objects
    p.bank_provider = lambda *a: pytest.fail("no runtime asset access")
    parameters = settled(speed_kmh=80)
    for i in range(1201):
        mixer.now = i / 60
        p.update(parameters, mixer.now)
    assert (mixer.sound_count, mixer.channel_objects) == counts
    assert p.launches == 3 and p.underruns == 0
    for channel, layer in zip(p.channels, p.layers):
        assert channel.history == [layer[i % len(layer)] for i in range(21)]
    assert p.queue_count == 63
    assert mixer.reserved == 3 and mixer.get_num_channels() == 11


def test_missing_observations_fade_and_do_not_replenish_queue(player):
    p, mixer = player
    params = settled(speed_kmh=70)
    p.update(params, 0)
    mixer.now = 0.6
    p.update(replace(params, fresh=False, gains=(0, 0, 0)), 0.6)
    assert p.queue_count == 3
    assert not any(c.get_busy() for c in p.channels)
    mixer.now = 0.7
    p.update(params, 0.7)
    assert p.available and all(c.get_busy() for c in p.channels)


def test_python_stall_has_finite_two_second_tail_without_cleanup(player):
    p, mixer = player
    p.update(settled(speed_kmh=70), 0)
    mixer.now = 2.01
    assert not any(c.get_busy() for c in p.channels)
    assert all(c.stops == 0 for c in p.channels)


def test_warning_ducks_current_and_queued_clips_and_leaves_indicator_alone(
    player, monkeypatch
):
    p, mixer = player
    monkeypatch.setattr(pygame, "mixer", mixer)
    params = settled(speed_kmh=90)
    p.update(params, 0)
    normal = p.layers[2][0].volume
    click = TurnSignalAudio()
    click.update(active=True, now=0)
    cue = HazardWarningAudio(mixer=mixer)
    cue.prepare()
    result = cue.play()
    assert (
        result["play_request_accepted"]
        and result["vehicle_audio_ducking"]["status"] == "APPLIED"
    )
    assert all(s.volume <= normal * WARNING_DUCK_GAIN + 1 / 128 for s in p.layers[2])
    assert p.channels[2].sound.volume < 0.04
    assert p.channels[2].get_queue().volume < 0.04
    assert click._sound.volume == cue.sound.volume == 1
    assert click._sound.selected >= 3 and cue.sound.selected != click._sound.selected
    mixer.now = 0.8
    p.update(params, mixer.now)
    assert p.layers[2][0].volume < 0.04
    mixer.now = 1.5
    p.update(params, mixer.now)
    assert p.layers[2][0].volume == normal
    assert p.duck_requests == 1
    p.close(fade=False)
    assert all(c.stops == 0 for c in mixer.channels[3:])
    click.close()
    cue.close()


@pytest.mark.parametrize(
    "failure",
    [
        "init",
        "sound",
        "reserve",
        "volume",
        "play",
        "queue",
        "get_queue",
        "stop",
        "asset",
    ],
)
def test_audio_failures_are_nonfatal_and_owner_released(failure):
    mixer = RecordedFakeMixer(None if failure == "init" else (44100, -16, 2))
    p = RecordedVehicleAudioPlayback(
        mixer, bank_provider=recorded_test_bank, wait=mixer.wait
    )
    if failure in {"init", "sound", "reserve", "volume"}:
        mixer.fail = failure
    if failure == "asset":

        def missing(*args):
            raise FileNotFoundError("missing recorded WAV")

        p.bank_provider = missing
    p.prepare()
    if failure in {"play", "queue", "get_queue", "stop"}:
        mixer.fail = failure
    p.update(settled(), 0)
    p.close(fade=False)
    p.close(fade=False)
    assert p.error or p.cleanup_errors
    assert p.closed and RecordedVehicleAudioPlayback._owner is None


def test_duck_failure_never_prevents_warning(player, monkeypatch):
    p, mixer = player
    p.update(settled(), 0)
    cue = HazardWarningAudio(mixer=mixer)
    cue.prepare()
    mixer.fail = "volume"
    result = cue.play()
    assert result["play_request_accepted"]
    assert result["vehicle_audio_ducking"]["status"] == "VEHICLE_AUDIO_DISABLED"
    assert p.closed
    cue.close()


def test_even_duck_hook_exception_never_prevents_warning(monkeypatch):
    def broken(*args):
        raise RuntimeError("optional duck hook failure")

    monkeypatch.setattr(hazard_warning_audio, "duck_vehicle_audio_for_warning", broken)
    cue = HazardWarningAudio(mixer=RecordedFakeMixer())
    cue.prepare()
    assert cue.play()["play_request_accepted"]
    assert cue.summary()["errors"]
    cue.close()


def test_source_failure_and_identity_change_disable_audio_only(player):
    p, mixer = player
    runtime = RecordedVehicleAudioRuntime(
        lambda: state(mixer.now), p, clock=lambda: mixer.now
    )
    runtime.update()
    runtime.source = lambda: state(mixer.now, stage_id="M1")
    runtime.update()
    report = runtime.status()
    assert report["error"] and report["session_action"] == "CONTINUE" and p.closed
    assert report["asset"] == "honda-crv-recorded-preview-v1"


@pytest.mark.parametrize(
    "rate,channels", [(22050, 1), (22050, 2), (44100, 2), (48000, 2)]
)
def test_actual_approved_assets_decode_to_exact_chunks_with_existing_dummy_mixer(
    rate, channels
):
    pygame.mixer.quit()
    pygame.mixer.init(frequency=rate, size=-16, channels=channels)
    try:
        bank = recorded_bank(pygame.mixer, pygame.mixer.get_init())
        assert [len(layer) for layer in bank.layers] == [12, 6, 12]
        assert all(
            len(pcm) == rate * channels * 2 for layer in bank.layers for pcm in layer
        )
    finally:
        pygame.mixer.quit()


def test_asset_checksums_seams_levels_and_warning_headroom():
    manifest = json.loads(
        (ASSET_DIRECTORY / "manifest.json").read_text(encoding="utf-8")
    )
    for layer in manifest["layers"]:
        path = ASSET_DIRECTORY / layer["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == layer["sha256"]
        with wave.open(str(path), "rb") as reader:
            pcm = array("h", reader.readframes(reader.getnframes()))
        peak = max(abs(x) for x in pcm)
        rms_db = 20 * math.log10(math.sqrt(sum(x * x for x in pcm) / len(pcm)) / 32768)
        assert -25.1 < rms_db < -24.9 and peak <= 12000
        # The wrap is an ordinary adjacent-sample step, not an abrupt full-scale edge.
        assert max(abs(pcm[-2 + c] - pcm[c]) for c in range(2)) < 1000
    warning_peak = max(array("h", warning_pcm(44100, 2)))
    # Conservative bound assumes all three layers peak at once; weights sum <= .55.
    engine_bound = 12000 * (MASTER_GAIN * WARNING_DUCK_GAIN + 3 / 256)
    assert 20 * math.log10(warning_peak / engine_bound) > 20


def test_missing_and_corrupt_assets_raise_before_playback(tmp_path):
    with pytest.raises(FileNotFoundError):
        recorded_bank(None, (44100, -16, 2), tmp_path)
    shutil.copyfile(ASSET_DIRECTORY / "manifest.json", tmp_path / "manifest.json")
    (tmp_path / "idle.wav").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        recorded_bank(None, (44100, -16, 2), tmp_path)
