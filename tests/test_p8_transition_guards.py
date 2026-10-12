import csv
import json

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction_types import DriverInput
from src.p8.assignment import development_assignment
from src.p8.model import RECEIPTS, SEQUENCE, P8Error, Stage
from src.p8.orchestrator import SessionManager
from src.p8.runner import run_development


def enter_m1(session):
    while session.state is not Stage.MODULE_1_RUNNING:
        for name in RECEIPTS.get(session.state, ()):
            session.acknowledge(name, "DEV_STUB")
        session.transition(SEQUENCE[SEQUENCE.index(session.state) + 1])


def test_duration_and_exit_guards_are_independent(tmp_path):
    session = SessionManager(tmp_path).create(development_assignment(), "guard")
    try:
        enter_m1(session)
        session.components.exit_opportunity()
        with pytest.raises(P8Error, match="DURATION"):
            session.transition(Stage.MODULE_1_COMPLETE)
        with pytest.raises(P8Error, match="ALREADY"):
            session.components.exit_opportunity()
    finally:
        session.abort("TEST_END")


def test_active_m1_end_cannot_leak_into_rest_or_m2_manual(tmp_path):
    session = SessionManager(tmp_path).create(development_assignment(), "active-end")
    try:
        enter_m1(session)
        session.components.automation.input(DriverInput(activation_requested=True))
        session.components.exit_opportunity()
        session.services.timeline.advance(4)
        session.transition(Stage.MODULE_1_COMPLETE)
        assert session.components.automation.controller.state.control_mode is DrivingControlMode.MANUAL
        session.transition(Stage.REST)
        with pytest.raises(P8Error, match="DURATION"):
            session.transition(Stage.MODULE_2_PREPARE)
        session.services.timeline.advance(2)
        session.transition(Stage.MODULE_2_PREPARE)
        session.transition(Stage.MODULE_2_RUNNING)
        assert session.components.automation.modules["MODULE_2"]["initial_mode"] == "MANUAL"
    finally:
        session.abort("TEST_END")


def test_common_logger_failure_does_not_leave_p6_writers_running(tmp_path, monkeypatch):
    session = SessionManager(tmp_path).create(development_assignment(), "failed-event-log")
    enter_m1(session)
    session.components.tick()

    def fail_event(*args, **kwargs):
        raise OSError("injected common event failure")

    monkeypatch.setattr(session.services, "emit", fail_event)

    session.abort("OPERATOR_REQUEST")

    assert session.closed
    assert session.components.p6._closed
    assert session.components.p6.gaze._thread is None
    assert session.components.p6.gaze.path.stat().st_size > 0
    assert session.result["completion_status"] == "ABORTED"


def test_failed_module_initialization_is_attempt_not_normal_stage_start(tmp_path, monkeypatch):
    session = SessionManager(tmp_path).create(development_assignment(), "init-failure")
    enter = session.components.enter

    def fail_module(stage):
        if stage is Stage.MODULE_2_RUNNING:
            raise P8Error("P3_INITIALIZATION_FAILED")
        enter(stage)

    monkeypatch.setattr(session.components, "enter", fail_module)

    with pytest.raises(P8Error, match="INITIALIZATION"):
        run_development(session)

    with session.services.logger.event_path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    starts = [json.loads(row["event_value"])["stage"] for row in rows if row["event_type"] == "stage_start"]
    assert "MODULE_2_RUNNING" not in starts
    assert session.result["completion_status"] == "ABORTED"
