from array import array
from dataclasses import replace
from types import SimpleNamespace

import pygame
import pytest

from src.scenario.turn_signal_audio import TurnSignalAudio
from src.scenario.vehicle_audio_assets import (
    GRAIN_SECONDS,
    NOISE_INDEX,
    PHASE_COUNT,
    VOICE_COUNT,
    procedural_bank,
    voice_mix,
)
from src.scenario.vehicle_audio_model import EngineSoundModel
from src.scenario.vehicle_audio_playback import VehicleAudioPlayback
from src.scenario.vehicle_audio_runtime import (
    CachedVehicleAudioSource,
    VehicleAudioRuntime,
)
from tests.test_vehicle_audio_model import settle, state
from tests.vehicle_audio_fakes import FakeMixer, cached_sample, small_bank


@pytest.fixture
def playback():
    mixer = FakeMixer()
    player = VehicleAudioPlayback(mixer, bank_provider=small_bank, wait=mixer.wait)
    yield player, mixer
    player.close(fade=False)


def test_preload_once_bounded_channels_no_sound_or_channel_objects_in_updates(playback):
    player, mixer = playback
    player.prepare()
    player.prepare()
    counts = (mixer.sound_count, mixer.channel_objects)
    assert counts[0] == NOISE_INDEX + 2 and mixer.init_count == 0
    params = settle(speed_kmh=60, gear=3, throttle=0.6)
    for i in range(600):
        mixer.now = i / 60
        player.update(params, mixer.now)
    assert 55 <= player.launches <= 65
    assert (mixer.sound_count, mixer.channel_objects) == counts
    assert mixer.get_num_channels() == VOICE_COUNT + 8
    assert player.skipped_busy == 0


def test_click_automatic_channel_survives_engine_close(playback, monkeypatch):
    player, mixer = playback
    monkeypatch.setattr(pygame, "mixer", mixer)
    player.prepare()
    player.update(settle(speed_kmh=60, throttle=0.8), 0)
    click = TurnSignalAudio()
    click.update(active=True, now=0)
    selected = click._sound.selected
    assert selected >= VOICE_COUNT
    player.close(fade=False)
    assert mixer.channels[selected].get_busy()
    assert mixer.channels[selected].stops == 0
    click.close()


def test_stalled_python_loop_expires_without_any_update_or_stop(playback):
    player, mixer = playback
    player.prepare()
    player.update(settle(speed_kmh=90, throttle=0.7), 0)
    assert any(c.get_busy() for c in mixer.channels)
    mixer.now = GRAIN_SECONDS + 0.01
    assert not any(c.get_busy() for c in mixer.channels)
    assert all(c.stops == 0 for c in mixer.channels)


def test_crossfade_uses_overlapping_voices_and_mixes_pitch_and_load(playback):
    player, mixer = playback
    player.prepare()
    p = settle(speed_kmh=50, gear=3, throttle=0.45)
    mix = voice_mix(p)
    assert sum(gain for _, gain in mix[:4]) == pytest.approx(p.engine_volume)
    assert all(gain > 0 for _, gain in mix[:4])
    player.update(p, 0)
    mixer.now = 0.16
    player.update(replace(p, rpm=p.rpm + 300), mixer.now)
    assert sum(c.get_busy() for c in mixer.channels) == 12


@pytest.mark.parametrize("failure", ["init", "sound", "reserve", "play", "volume"])
def test_mixer_failure_disables_optional_audio_without_retries(failure):
    mixer = FakeMixer(None if failure == "init" else (22050, -16, 1), failure)
    player = VehicleAudioPlayback(mixer, bank_provider=small_bank, wait=mixer.wait)
    player.prepare()
    player.update(settle(), 0)
    player.prepare()
    assert player.error is not None and player.closed
    assert VehicleAudioPlayback._owner is None
    assert mixer.init_count <= 1


def test_uninitialized_mixer_initializes_once_and_reuses_format():
    mixer = FakeMixer(None)
    player = VehicleAudioPlayback(mixer, bank_provider=small_bank, wait=mixer.wait)
    try:
        player.prepare()
        player.prepare()
        assert mixer.init_count == 1 and player.format == (22050, -16, 2)
    finally:
        player.close()


def test_busy_existing_channel_is_never_stolen_or_stopped(playback):
    player, mixer = playback
    mixer.channels[0].until = 100
    player.prepare()
    assert not player.available
    assert mixer.channels[0].get_busy() and mixer.channels[0].stops == 0
    assert mixer.get_num_channels() == 8


def test_new_stage_gets_new_sounds_and_releases_reservation(playback):
    player, mixer = playback
    player.prepare()
    player.update(settle(), 0)
    player.close()
    assert mixer.now == 0.05 and mixer.reserved == 0
    assert not any(c.get_busy() for c in mixer.channels)
    new = VehicleAudioPlayback(mixer, bank_provider=small_bank, wait=mixer.wait)
    try:
        new.prepare()
        assert new.available and mixer.sound_count == 2 * (NOISE_INDEX + 2)
        assert mixer.get_num_channels() == VOICE_COUNT + 8
    finally:
        new.close(fade=False)


def test_stop_failure_is_retained_and_all_channels_attempted(playback):
    player, mixer = playback
    player.prepare()
    mixer.fail = "stop"
    player.close()
    assert len(player.cleanup_errors) == VOICE_COUNT
    assert all(c.stops == 1 for c in mixer.channels[:VOICE_COUNT])
    assert VehicleAudioPlayback._owner is None


def test_mixer_loss_and_stale_state_do_not_restart_output(playback):
    player, mixer = playback
    player.prepare()
    player.update(replace(settle(), fresh=False), 1)
    assert player.launches == 0
    mixer.config = None
    player.update(settle(), 2)
    assert player.closed and "mixer" in player.error


def test_source_reuses_snapshot_applied_control_and_does_not_refresh_stalled_frame():
    persistence = SimpleNamespace(
        source=SimpleNamespace(last_snapshot_sample=cached_sample()),
        segment=SimpleNamespace(segment_id="M1"),
    )
    source = CachedVehicleAudioSource(persistence, 123, lambda: "NOA_ACTIVE")
    first = source()
    assert first.throttle == 0.7 and first.gear == 3 and first.mode == "NOA_ACTIVE"
    persistence.source.last_snapshot_sample = cached_sample(stamp=3)
    assert source().observed_at == first.observed_at
    assert not EngineSoundModel().update(source(), 3).fresh
    persistence.source.last_snapshot_sample = cached_sample(
        frame=2, stamp=3, throttle=0.2
    )
    assert source().observed_at == 3 and source().throttle == 0.2


def test_missing_actor_observation_and_source_failure_are_audio_only(playback):
    player, _mixer = playback
    runtime = VehicleAudioRuntime(lambda: None, player, clock=lambda: 1)
    runtime.prepare()
    runtime.update()
    assert runtime.fresh_updates == 0 and player.launches == 0
    runtime.source = lambda: (_ for _ in ()).throw(RuntimeError("actor gone"))
    runtime.update()
    assert "actor gone" in runtime.error and player.closed


def test_runtime_identity_boundary_closes_old_voices(playback):
    player, _mixer = playback
    current = [state()]
    runtime = VehicleAudioRuntime(lambda: current[0], player, clock=lambda: 0)
    runtime.prepare()
    runtime.update()
    current[0] = replace(current[0], stage_id="M2")
    runtime.update()
    assert player.closed and "identity" in runtime.error


def test_original_assets_have_smooth_edges_multiple_harmonics_and_distinct_loads():
    bank = procedural_bank(22050, 1)
    assert bank is procedural_bank(22050, 1)
    assert len(bank.pcm) == NOISE_INDEX + 2
    for pcm in bank.pcm:
        samples = array("h", pcm)
        assert samples[0] == 0 and abs(samples[-1]) <= 1
        assert max(abs(v) for v in samples) < 32767
        assert max(abs(v) for v in samples) > 100
    assert bank.pcm[0] != bank.pcm[PHASE_COUNT] != bank.pcm[2 * PHASE_COUNT]
    # Explicit DFT around harmonic frequencies on the constant middle of an idle grain.
    from math import cos, sin, tau

    samples = array("h", bank.pcm[PHASE_COUNT])
    energy = []
    for frequency in (25, 50, 75, 100):
        re = sum(v * cos(tau * frequency * i / 22050) for i, v in enumerate(samples))
        im = sum(v * sin(tau * frequency * i / 22050) for i, v in enumerate(samples))
        energy.append((re * re + im * im) ** 0.5)
    assert min(energy) > max(energy) * 0.1


@pytest.mark.parametrize(
    "rate,channels", [(22050, 1), (22050, 2), (44100, 2), (48000, 1)]
)
def test_format_provider_contract_without_runtime_resampling(rate, channels):
    bank = small_bank(rate, channels)
    assert len(bank.pcm[0]) == round(rate * GRAIN_SECONDS) * channels * 2


def test_unsupported_format_fails_without_changing_existing_mixer():
    mixer = FakeMixer((44100, 32, 2))
    player = VehicleAudioPlayback(mixer, bank_provider=small_bank)
    player.prepare()
    assert not player.available and mixer.init_count == 0
    assert mixer.config == (44100, 32, 2)
