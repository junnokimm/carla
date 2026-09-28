from __future__ import annotations

import pygame


class FakeSound:
    def __init__(self) -> None:
        self.play_count = 0
        self.stop_count = 0

    def play(self) -> None:
        self.play_count += 1

    def stop(self) -> None:
        self.stop_count += 1


def test_audio_plays_one_click_per_interval_only_while_blinker_is_active(
    monkeypatch,
) -> None:
    from src.scenario.turn_signal_audio import TurnSignalAudio

    sound = FakeSound()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (22_050, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Sound", lambda *, buffer: sound)
    audio = TurnSignalAudio()

    audio.update(active=False, now=0.0)
    audio.update(active=True, now=0.1)
    audio.update(active=True, now=0.89)
    audio.update(active=True, now=0.9)
    audio.update(active=True, now=0.91)

    assert sound.play_count == 2


def test_audio_deactivation_cancels_schedule_and_reactivation_clicks_immediately(
    monkeypatch,
) -> None:
    from src.scenario.turn_signal_audio import TurnSignalAudio

    sound = FakeSound()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (22_050, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Sound", lambda *, buffer: sound)
    audio = TurnSignalAudio()

    audio.update(active=True, now=0.0)
    audio.update(active=False, now=0.1)
    audio.update(active=True, now=0.2)

    assert sound.play_count == 2


def test_audio_close_stops_sound_and_prevents_more_playback(monkeypatch) -> None:
    from src.scenario.turn_signal_audio import TurnSignalAudio

    sound = FakeSound()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (22_050, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Sound", lambda *, buffer: sound)
    audio = TurnSignalAudio()
    audio.update(active=True, now=0.0)

    audio.close()
    audio.update(active=True, now=1.0)

    assert sound.play_count == 1
    assert sound.stop_count == 1


def test_audio_disables_itself_when_mixer_initialization_fails(monkeypatch) -> None:
    from src.scenario.turn_signal_audio import TurnSignalAudio

    init_calls = 0

    def fail_init(**kwargs) -> None:
        nonlocal init_calls
        init_calls += 1
        raise pygame.error("no audio device")

    monkeypatch.setattr(pygame.mixer, "get_init", lambda: None)
    monkeypatch.setattr(pygame.mixer, "init", fail_init)
    audio = TurnSignalAudio()

    audio.update(active=True, now=0.0)
    audio.update(active=True, now=1.0)

    assert init_calls == 1


def test_audio_disables_itself_when_playback_fails(monkeypatch) -> None:
    from src.scenario.turn_signal_audio import TurnSignalAudio

    sound = FakeSound()

    def fail_play() -> None:
        sound.play_count += 1
        raise pygame.error("audio device disconnected")

    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (22_050, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Sound", lambda *, buffer: sound)
    monkeypatch.setattr(sound, "play", fail_play)
    audio = TurnSignalAudio()

    audio.update(active=True, now=0.0)
    audio.update(active=True, now=1.0)

    assert sound.play_count == 1


def test_audio_close_ignores_mixer_shutdown_failure(monkeypatch) -> None:
    from src.scenario.turn_signal_audio import TurnSignalAudio

    sound = FakeSound()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (22_050, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Sound", lambda *, buffer: sound)
    audio = TurnSignalAudio()
    audio.update(active=True, now=0.0)
    monkeypatch.setattr(
        sound,
        "stop",
        lambda: (_ for _ in ()).throw(pygame.error("mixer stopped")),
    )

    audio.close()

    assert sound.play_count == 1
