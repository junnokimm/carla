import subprocess
import sys
from pathlib import Path

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.experiment.context import ExperimentModule, Module2Condition
from src.scenario.research_noa_persistence import PersistedInteractionObserver
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_automation_interaction import make_controller
from tests.test_p4_research_noa_integration import make_config, read_dicts
from tests.test_r1_r4_runtime_boundaries import (
    FailingPersistence,
    PrimaryInteractionError,
    RecordingController,
)


def test_initialize_preserves_primary_when_event_flush_also_fails(monkeypatch) -> None:
    controller = RecordingController([])
    primary = PrimaryInteractionError("initialization")

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(controller, "initialize", fail)
    observer = PersistedInteractionObserver(controller, FailingPersistence([]))
    with pytest.raises(PrimaryInteractionError) as caught:
        observer.initialize(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)
    assert caught.value is primary


def test_shutdown_timestamp_precedes_delayed_pending_event_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeMonotonicClock()
    factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_config(tmp_path),
        client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
        viewer_factory=factory,
        monotonic_clock=clock,
        monotonic_ns=lambda: round(clock() * 1_000_000_000),
        utc_ns=lambda: 1,
    )
    with runner.session() as session:
        assert session.interaction is not None
        assert session.persistence is not None
        original = session.persistence.logger.write_event

        def slow_write(*args, **kwargs):
            clock.advance(0.2)
            return original(*args, **kwargs)

        def activate_then_fail() -> None:
            clock.advance(1.0)
            session.interaction.update(DriverInput(activation_requested=True))
            monkeypatch.setattr(session.persistence.logger, "write_event", slow_write)
            raise PrimaryInteractionError("stop with pending events")

        factory.created[0].actions = [activate_then_fail]
        with pytest.raises(PrimaryInteractionError):
            session.run()

    assert runner.log_paths is not None
    shutdown = next(
        row for row in read_dicts(runner.log_paths.events)
        if row["event_value"] == "SAFETY_SHUTDOWN"
    )
    assert float(shutdown["session_elapsed_s"]) == 1.0


def test_m2_manual_preserves_development_reactivation_policy() -> None:
    controller, _, _ = make_controller()
    controller.initialize(ExperimentModule.MODULE_2, Module2Condition.MANUAL)
    assert controller.state.control_mode is DrivingControlMode.MANUAL
    controller.update(DriverInput(activation_requested=True))
    assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_synthetic_600_second_ratio_is_recomputed_from_saved_events(tmp_path: Path) -> None:
    clock = FakeMonotonicClock()
    factory = FakeDriverViewFactory()
    inputs = iter((
        DriverInput(activation_requested=True), DriverInput(brake=0.1),
        DriverInput(activation_requested=True), DriverInput(brake=0.1), DriverInput(),
    ))
    runner = ResearchNoARunner(
        make_config(tmp_path),
        client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
        viewer_factory=factory, driver_input_source=inputs.__next__,
        monotonic_clock=clock,
        monotonic_ns=lambda: round(clock() * 1_000_000_000), utc_ns=lambda: 1,
    )
    with runner.session() as session:
        factory.created[0].actions = [
            lambda step=step: clock.advance(step) for step in (90, 120, 150, 120, 120)
        ]
        session.run()
    assert runner.log_paths is not None
    transitions = [
        row for row in read_dicts(runner.log_paths.events)
        if row["event_type"] == "auto_state"
    ]
    assert [float(row["session_elapsed_s"]) for row in transitions] == [90, 210, 360, 480]
    on_seconds = sum(
        float(end["session_elapsed_s"]) - float(start["session_elapsed_s"])
        for start, end in zip(transitions[::2], transitions[1::2], strict=True)
    )
    assert on_seconds == 240
    assert on_seconds / 600 == 0.4


def test_abrupt_child_exit_preserves_flushed_rows(tmp_path: Path) -> None:
    code = """
import os, sys
from pathlib import Path
from src.scenario.research_noa_log_setup import open_research_log
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchEvent
from src.scenario.research_noa_persistence import build_segment
from tests.test_p4_research_noa_integration import make_config
config = make_config(Path(sys.argv[1])).persistence
opened = open_research_log(config, lambda: 0, lambda: 0)
opened.logger.write_event(build_segment(config), TimestampEnvelope(HostClockTimestamp(10, 10)), ResearchEvent('auto_request', 'NOA_ACTIVE'))
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    assert result.returncode == 9
    rows = read_dicts(tmp_path / "research_events_run-p4.csv")
    assert [row["event_type"] for row in rows] == ["assignment", "auto_request"]
