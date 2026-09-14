from dataclasses import dataclass

import pytest

from src.vehicle import VehicleState


@dataclass
class FakeClock:
    current: float = 0.0

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


class FakeVehicleClient:
    def __init__(self, states: list[VehicleState]) -> None:
        self._states = iter(states)

    def get_state(self) -> VehicleState:
        return next(self._states)


class InterruptingVehicleClient:
    def get_state(self) -> VehicleState:
        raise KeyboardInterrupt


class FailingVehicleClient:
    def get_state(self) -> VehicleState:
        raise RuntimeError("CARLA connection lost")


class RecordingLogger:
    def __init__(self) -> None:
        self.states: list[VehicleState] = []
        self.closed = False

    def write(self, state: VehicleState) -> None:
        self.states.append(state)

    def close(self) -> None:
        self.closed = True


def test_run_logging_session_writes_expected_samples_and_closes(monkeypatch):
    from src.vehicle import log_hero_state

    clock = FakeClock()
    state = VehicleState(1.0, 36.0, 0.1, 0.2, 0.0, 1, "off")
    logger = RecordingLogger()
    monkeypatch.setattr(log_hero_state.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(log_hero_state.time, "sleep", clock.sleep)

    result = log_hero_state.run_logging_session(
        FakeVehicleClient([state, state, state]),
        logger,
        log_hero_state.LiveLoggingConfig(duration=0.25, sample_interval=0.1),
    )

    assert result.sample_count == 3
    assert result.interrupted is False
    assert logger.states == [state, state, state]
    assert logger.closed is True


def test_run_logging_session_closes_logger_when_interrupted(monkeypatch):
    from src.vehicle import log_hero_state

    clock = FakeClock()
    logger = RecordingLogger()
    monkeypatch.setattr(log_hero_state.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(log_hero_state.time, "sleep", clock.sleep)

    result = log_hero_state.run_logging_session(
        InterruptingVehicleClient(),
        logger,
        log_hero_state.LiveLoggingConfig(duration=1.0, sample_interval=0.1),
    )

    assert result.sample_count == 0
    assert result.interrupted is True
    assert logger.closed is True


def test_run_logging_session_closes_logger_when_vehicle_client_fails(monkeypatch):
    from src.vehicle import log_hero_state

    clock = FakeClock()
    logger = RecordingLogger()
    monkeypatch.setattr(log_hero_state.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(log_hero_state.time, "sleep", clock.sleep)

    with pytest.raises(RuntimeError, match="CARLA connection lost"):
        log_hero_state.run_logging_session(
            FailingVehicleClient(),
            logger,
            log_hero_state.LiveLoggingConfig(duration=1.0, sample_interval=0.1),
        )

    assert logger.closed is True


def test_parse_arguments_uses_provided_session_and_timing_values():
    from src.vehicle import log_hero_state

    config, session_id = log_hero_state.parse_arguments(
        ["--duration", "5", "--sample-interval", "0.25", "--session-id", "test01"]
    )

    assert config == log_hero_state.LiveLoggingConfig(5.0, 0.25)
    assert session_id == "test01"
