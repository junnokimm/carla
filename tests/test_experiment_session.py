import csv

import pytest

from src.experiment.scheduler import EventScheduler
from src.experiment.session import ExperimentSession
from src.logging.csv_logger import VehicleStateCsvLogger
from src.vehicle import MockVehicleClient


def test_experiment_session_completes_and_records_samples(tmp_path):
    logger = VehicleStateCsvLogger("session", tmp_path / "data")
    session = ExperimentSession(
        MockVehicleClient(), logger, EventScheduler(0, 1.0, 1.0, 1), duration=2.0
    )

    result = session.run(sample_interval=1.0)

    assert result.completed is True
    assert result.sample_count == 3
    with logger.path.open(newline="") as session_file:
        assert len(list(csv.reader(session_file))) == 4


def test_experiment_session_triggers_due_events_in_order(tmp_path):
    logger = VehicleStateCsvLogger("session", tmp_path / "data")
    session = ExperimentSession(
        MockVehicleClient(), logger, EventScheduler(2, 1.0, 1.0, 1), duration=3.0
    )

    result = session.run(sample_interval=1.0)

    assert result.triggered_event_ids == (1, 2)


def test_experiment_session_does_not_trigger_events_before_due_time(tmp_path):
    logger = VehicleStateCsvLogger("session", tmp_path / "data")
    session = ExperimentSession(
        MockVehicleClient(), logger, EventScheduler(1, 1.0, 1.0, 1), duration=0.5
    )

    result = session.run(sample_interval=0.5)

    assert result.triggered_event_ids == ()


def test_experiment_session_closes_logger_when_complete(tmp_path):
    logger = VehicleStateCsvLogger("session", tmp_path / "data")
    session = ExperimentSession(
        MockVehicleClient(), logger, EventScheduler(0, 1.0, 1.0, 1), duration=0.0
    )

    session.run(sample_interval=1.0)

    with pytest.raises(ValueError):
        logger.write(MockVehicleClient().get_state())


def test_experiment_session_is_deterministic(tmp_path):
    first = ExperimentSession(
        MockVehicleClient(),
        VehicleStateCsvLogger("first", tmp_path / "data"),
        EventScheduler(2, 1.0, 2.0, 42),
        duration=4.0,
    ).run(sample_interval=1.0)
    second = ExperimentSession(
        MockVehicleClient(),
        VehicleStateCsvLogger("second", tmp_path / "data"),
        EventScheduler(2, 1.0, 2.0, 42),
        duration=4.0,
    ).run(sample_interval=1.0)

    assert first == second
