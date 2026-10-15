from __future__ import annotations

import csv
import json
from pathlib import Path

import carla
import pytest

from src.p8.model import Stage
from src.p8.operator_model import DevOutcome, TrainingRun
from tests.research_noa_fakes import FakeResearchWorld
from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev
from tests.test_p8_traffic_manager import _tm_runner_with_client


def test_operator_applies_clear_noon_before_each_stage_spawn_and_records_weather(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[carla.WeatherParameters, int]] = []

    def set_weather(world: FakeResearchWorld, weather: carla.WeatherParameters) -> None:
        applied.append((weather, len(world.spawn_calls)))
        world.weather = weather

    monkeypatch.setattr(FakeResearchWorld, "set_weather", set_weather, raising=False)
    monkeypatch.setattr(
        FakeResearchWorld, "get_weather", lambda world: world.weather, raising=False
    )
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)
    controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "weather regression")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    controller.execute_current_module(1.0)
    controller.abort("offline weather verification complete")

    assert len(applied) == 3
    for weather, spawn_count in applied:
        assert weather == carla.WeatherParameters.ClearNoon
        assert spawn_count == 0
    with controller.session.services.logger.event_path.open(
        newline="", encoding="utf-8"
    ) as source:
        rows = [
            json.loads(row["event_value"])
            for row in csv.DictReader(source)
            if row["event_type"] == "p8_weather_applied"
        ]
    assert len(rows) == 3
    assert all(row["preset"] == "ClearNoon" for row in rows)
    assert all(row["observed"]["precipitation"] == 0.0 for row in rows)
    assert all(
        row["observed"]["sun_altitude_angle"]
        == carla.WeatherParameters.ClearNoon.sun_altitude_angle
        for row in rows
    )


def test_weather_application_error_aborts_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[FakeResearchWorld] = []

    def unavailable(world: FakeResearchWorld, weather: carla.WeatherParameters) -> None:
        observed.append(world)
        raise RuntimeError("weather RPC failed")

    monkeypatch.setattr(FakeResearchWorld, "set_weather", unavailable, raising=False)
    controller, _ = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.TRAINING)
    with pytest.raises(RuntimeError, match="weather RPC failed"):
        controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    assert len(observed) == 1
    assert observed[0].spawn_calls == []
    assert controller.snapshot().stage is Stage.ABORTED


def test_nonoperator_runner_does_not_change_weather(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _, world, _ = _tm_runner_with_client()

    def forbidden(weather: carla.WeatherParameters) -> None:
        pytest.fail("non-P8 runtime must not change weather")

    monkeypatch.setattr(world, "set_weather", forbidden, raising=False)
    with runner.session():
        pass
