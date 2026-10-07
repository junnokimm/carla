from __future__ import annotations

import json

import carla

from src.scenario.research_camera_diagnostics import ResearchCameraDiagnostics
from tests.test_driver_view import FakeWorld
from tests.test_driver_view_turn_signals import FakeLightHero, FakeTurnSignalAudio


class StepClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)
        self.call_count = 0

    def __call__(self) -> float:
        self.call_count += 1
        return next(self._values)


def test_update_preserves_state_logic_without_diagnostics(monkeypatch) -> None:
    from src.scenario import driver_view

    audio = FakeTurnSignalAudio()
    hero = FakeLightHero()
    hero.light_state = carla.VehicleLightState.LeftBlinker
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: 12.5)
    viewer = driver_view.DriverView(FakeWorld(), hero)

    viewer._update_turn_signal_audio()

    assert audio.updates == [(True, 12.5)]


def test_update_summarizes_first_and_steady_split_intervals(monkeypatch) -> None:
    from src.scenario import driver_view

    diagnostic_clock = StepClock(
        1.0,
        1.002,
        2.0,
        2.004,
        3.0,
        3.006,
        4.0,
        4.008,
        5.0,
        5.01,
        6.0,
        6.012,
        7.0,
    )
    monotonic_clock = StepClock(10.0, 11.0, 12.0)
    diagnostics = ResearchCameraDiagnostics(
        clock=diagnostic_clock,
        composition="front_only_1_rgb",
    )
    audio = FakeTurnSignalAudio()
    hero = FakeLightHero()
    monkeypatch.setattr(driver_view, "TurnSignalAudio", lambda: audio)
    monkeypatch.setattr(driver_view.time, "monotonic", monotonic_clock)
    viewer = driver_view.DriverView(
        FakeWorld(),
        hero,
        performance_observer=diagnostics,
    )

    viewer._update_turn_signal_audio()
    hero.light_state = carla.VehicleLightState.RightBlinker
    viewer._update_turn_signal_audio()
    hero.light_state = carla.VehicleLightState.NONE
    viewer._update_turn_signal_audio()
    payload = json.loads(diagnostics.to_json(()))

    assert audio.updates == [(False, 10.0), (True, 11.0), (False, 12.0)]
    assert payload["turn_signal_audio_timing"] == {
        "audio_update": {
            "first_call": {
                "count": 1,
                "mean_ms": 4.0,
                "max_ms": 4.0,
            },
            "subsequent_calls": {
                "count": 2,
                "mean_ms": 10.0,
                "max_ms": 12.0,
            },
        },
        "light_state_rpc": {
            "first_call": {
                "count": 1,
                "mean_ms": 2.0,
                "max_ms": 2.0,
            },
            "subsequent_calls": {
                "count": 2,
                "mean_ms": 8.0,
                "max_ms": 10.0,
            },
        },
    }
    assert payload["turn_signal_audio_timing_definitions"] == {
        "light_state_rpc": "hero.get_light_state call only",
        "audio_update": "TurnSignalAudio.update call only",
        "first_call": "first observed call for each operation",
        "subsequent_calls": "all observed calls after the first for each operation",
        "units": "count and milliseconds",
    }
    assert payload["clocks"]["host"] == "injected host clock seconds"
    assert payload["clocks"]["host_implementation"] == "unknown"
    assert diagnostic_clock.call_count == 13
    assert monotonic_clock.call_count == 3
