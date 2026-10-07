from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import carla
import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.session_clock import SessionClockError
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchEvent
from src.scenario import driver_view
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_view import ResearchLiveDriverView
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_driver_view_runtime import FakeFrameClock
from tests.test_p4_research_noa_integration import IncrementingNanoseconds, make_config


def test_real_view_n_brake_reactivation_precedes_slow_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real runner/view/controller and a synthetic common host clock.
    clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    keys: defaultdict[int, bool] = defaultdict(bool)
    collected: list[float] = []
    commands: list[tuple[float, float]] = []
    frames = iter((pygame.K_n, pygame.K_s, pygame.K_n, pygame.K_ESCAPE))

    def get_events() -> list[pygame.event.Event]:
        key = next(frames)
        keys[pygame.K_s] = key == pygame.K_s
        collected.append(clock())
        return [pygame.event.Event(pygame.KEYDOWN, key=key)]

    monkeypatch.setattr(driver_view.time, "monotonic", clock)
    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: None)
    monkeypatch.setattr(pygame.event, "get", get_events)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    monkeypatch.setattr(pygame.time, "Clock", FakeFrameClock)
    monkeypatch.setattr(pygame, "quit", lambda: None)
    monkeypatch.setattr(ResearchLiveDriverView, "attach", lambda self: None)
    monkeypatch.setattr(ResearchLiveDriverView, "_draw", lambda self, screen: None)
    monkeypatch.setattr(ResearchLiveDriverView, "_update_turn_signal_audio", lambda self: None)
    runner = ResearchNoARunner(
        replace(make_config(tmp_path), duration=20.0),
        client=FakeClient(world),
        monotonic_clock=clock,
        performance_clock=clock,
        monotonic_ns=lambda: round(clock() * 1_000_000_000),
        utc_ns=lambda: 1,
    )
    with runner.session() as session:
        assert session.persistence is not None
        original_waypoint = world.map.get_waypoint
        original_lights = vehicle.get_light_state
        original_write = session.persistence.logger.write_telemetry
        original_event = session.persistence.logger.write_event
        original_apply = vehicle.apply_control

        def slow_waypoint(*args, **kwargs):
            clock.advance(0.1)
            return original_waypoint(*args, **kwargs)

        def slow_lights():
            clock.advance(0.3)
            return original_lights()

        def slow_write(*args, **kwargs):
            clock.advance(0.2)
            return original_write(*args, **kwargs)

        def slow_event(*args, **kwargs):
            clock.advance(0.2)
            return original_event(*args, **kwargs)

        def record_apply(control: carla.VehicleControl) -> None:
            commands.append((clock(), control.brake))
            original_apply(control)

        monkeypatch.setattr(world.map, "get_waypoint", slow_waypoint)
        monkeypatch.setattr(vehicle, "get_light_state", slow_lights)
        monkeypatch.setattr(session.persistence.logger, "write_telemetry", slow_write)
        monkeypatch.setattr(session.persistence.logger, "write_event", slow_event)
        monkeypatch.setattr(vehicle, "apply_control", record_apply)

        # When: actual pygame event collection feeds N -> S -> N -> Escape.
        session.run()

        # Then: brake command is applied at input receipt, before any .6s capture.
        assert any(t == collected[1] and brake == 0.5 for t, brake in commands)
        assert session.viewer._research_config.automation_state_source is not None
        assert session.viewer._research_config.automation_state_source().control_mode is DrivingControlMode.MANUAL
        assert session.live_scheduler is not None
        assert session.live_scheduler.diagnostics.operation_timing("input_observer").count == 3
        assert session.live_scheduler.diagnostics.operation_timing("post_control").maximum_ms >= 600.0

    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="") as handle:
        events = list(csv.DictReader(handle))
    requests = [r for r in events if r["event_type"] == "auto_request"]
    transitions = [r for r in events if r["event_type"] == "auto_state"]
    assert len(requests) == 2
    assert float(requests[0]["session_elapsed_s"]) == pytest.approx(collected[0])
    assert float(transitions[0]["session_elapsed_s"]) > float(requests[0]["session_elapsed_s"])
    disengagement = next(r for r in events if r["event_value"] == "DRIVER_BRAKE")
    assert float(disengagement["session_elapsed_s"]) == pytest.approx(collected[1])
    assert all(r["carla_frame"] == "" for r in requests + transitions)
    with runner.log_paths.telemetry.open(newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 4
    assert vehicle.destroy_count == 1


def test_shared_logger_rejects_late_old_event_without_clamping(tmp_path: Path) -> None:
    # Given: a real logger whose common clock has already observed telemetry.
    runner = ResearchNoARunner(
        make_config(tmp_path), client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
        viewer_factory=FakeDriverViewFactory(),
        monotonic_ns=IncrementingNanoseconds(),
    )
    with runner.session() as session:
        assert session.persistence is not None
        persistence = session.persistence
        old = persistence.source.get_host_timestamp()
        persistence.capture()
        # When / Then: a backdated event is rejected, not silently clamped.
        with pytest.raises(SessionClockError, match="backwards"):
            persistence.logger.write_event(
                persistence.segment,
                TimestampEnvelope(HostClockTimestamp(old.host.monotonic_ns, 1)),
                ResearchEvent("auto_request", "NOA_ACTIVE"),
            )
