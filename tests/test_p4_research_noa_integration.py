from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.assignment import ParticipantAssignment
from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import (
    ExperimentModule,
    Module1Condition,
    Module2Condition,
    ParticipantId,
    StudyRunId,
)
from src.scenario.research_noa import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManagerRuntimeAttestor,
    make_live_control_config,
)


class IncrementingNanoseconds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 1_000_000
        return self.value


class InjectedP4RunError(RuntimeError):
    pass


class InjectedSnapshotError(RuntimeError):
    pass


class InjectedSummaryPersistenceError(RuntimeError):
    pass


class InjectedShutdownError(RuntimeError):
    pass


def read_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def make_config(output_dir: Path, run_id: str = "run-p4") -> ResearchNoARunConfig:
    assignment = ParticipantAssignment(
        ParticipantId("P001"),
        Module1Condition.NO_SURT,
        Module2Condition.NOA_L2,
        "Town04-route-1",
        "scenario-v1",
        "aoi-v1",
        "program-v1",
    )
    persistence = ResearchPersistenceConfig(
        assignment,
        StudyRunId(run_id),
        output_dir,
        ExperimentModule.MODULE_1,
        "development/module1-validation",
    )
    return ResearchNoARunConfig(
        duration=1.0,
        control_config=make_live_control_config(),
        mode=ResearchNoARunMode.LIVE_SMOKE,
        spawn_index=0,
        run_id=run_id,
        automation_interaction=ResearchAutomationInteractionConfig(
            ExperimentModule.MODULE_1,
            Module1Condition.NO_SURT,
            AutomationInteractionConfig(0.2, 0.1, 0.05, True),
            initial_stage="development/module1-validation",
        ),
        persistence=persistence,
    )


def make_module_two_config(output_dir: Path, run_id: str) -> ResearchNoARunConfig:
    config = make_config(output_dir, run_id)
    assert config.persistence is not None
    return replace(
        config,
        automation_interaction=ResearchAutomationInteractionConfig(
            ExperimentModule.MODULE_2,
            Module2Condition.NOA_L2,
            AutomationInteractionConfig(0.2, 0.1, 0.05, True),
            initial_stage="development/module2-validation",
        ),
        persistence=replace(
            config.persistence,
            module=ExperimentModule.MODULE_2,
            stage_label="development/module2-validation",
        ),
    )


def test_actual_runner_writes_assignment_events_and_observed_telemetry(
    tmp_path: Path,
) -> None:
    inputs = iter(
        (
            DriverInput(activation_requested=True, stage="activate"),
            DriverInput(brake=0.1, stage="brake"),
            DriverInput(activation_requested=True, stage="reactivate"),
        )
    )
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    clock = IncrementingNanoseconds()
    runner = ResearchNoARunner(
        make_config(tmp_path),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        driver_input_source=inputs.__next__,
        monotonic_ns=clock,
        utc_ns=lambda: 1_800_000_000_000_000_000,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None] * 3
        session.run()

    assert runner.log_paths is not None
    events = read_dicts(runner.log_paths.events)
    telemetry = read_dicts(runner.log_paths.telemetry)
    event_types = [row["event_type"] for row in events]
    assert event_types[0] == "assignment"
    assert "stage_start" in event_types
    assert "initial_state" in event_types
    assert event_types.count("auto_request") == 2
    assert "disengagement" in event_types
    assert event_types[-1] == "stage_end"
    assert all(row["participant_id"] == "P001" for row in events)
    assert all(row["run_id"] == "run-p4" for row in events)
    assert all(row["session_elapsed_s"] for row in events)
    assert all(row["assignment_route_id"] == "Town04-route-1" for row in events)
    assert all(row["scenario_version"] == "scenario-v1" for row in events)
    assert telemetry
    assert all(row["participant_id"] == "P001" for row in telemetry)
    assert all(row["run_id"] == "run-p4" for row in telemetry)
    assert all(row["session_elapsed_s"] for row in telemetry)
    assert all(row["carla_frame"] for row in telemetry)
    assert all(row["carla_simulation_seconds"] for row in telemetry)
    assert telemetry[0]["speed_kmh"] == "0.0"


def test_actual_viewer_run_persists_exactly_one_complete_performance_summary(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        replace(make_config(tmp_path, "run-performance"), camera_diagnostics=True),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        session.run()

    assert runner.log_paths is not None
    events = read_dicts(runner.log_paths.events)
    summaries = [row for row in events if row["event_type"] == "performance_summary"]
    assert len(summaries) == 1
    payload = json.loads(summaries[0]["event_value"])
    assert payload["status"] == "COMPLETE"
    assert payload["error"] is None
    assert events.index(summaries[0]) < next(
        index for index, row in enumerate(events) if row["event_type"] == "stage_end"
    )


def test_performance_summary_operations_cover_only_active_viewer_window(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        replace(make_config(tmp_path, "run-operation-window"), camera_diagnostics=True),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.live_scheduler is not None
        diagnostics = session.live_scheduler.diagnostics
        diagnostics.record_operation_duration("scheduler", 0.100)
        viewer_factory.created[0].actions = [
            lambda: diagnostics.record_operation_duration("scheduler", 0.020)
        ]
        session.run()

    assert runner.log_paths is not None
    events = read_dicts(runner.log_paths.events)
    summary = next(row for row in events if row["event_type"] == "performance_summary")
    operation = json.loads(summary["event_value"])["operations"]["scheduler"]
    assert operation == {
        "count": 1,
        "mean_ms": pytest.approx(20.0),
        "max_ms": pytest.approx(20.0),
    }


def test_exception_preserves_written_rows_records_stage_end_and_closes_files(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        replace(make_config(tmp_path, "run-error"), camera_diagnostics=True),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with (
        pytest.raises(InjectedP4RunError, match="viewer failed") as caught,
        runner.session() as session,
    ):
        def fail_viewer_and_final_snapshot() -> None:
            def fail_snapshot():
                raise InjectedSnapshotError("snapshot failed")

            world.get_snapshot = fail_snapshot
            raise InjectedP4RunError("viewer failed")

        viewer_factory.created[0].actions = [fail_viewer_and_final_snapshot]
        session.run()

    assert runner.log_paths is not None
    events = read_dicts(runner.log_paths.events)
    summaries = [row for row in events if row["event_type"] == "performance_summary"]
    assert len(summaries) == 1
    summary = json.loads(summaries[0]["event_value"])
    assert summary["status"] == "PARTIAL"
    assert summary["error"] == {
        "type": "InjectedP4RunError",
        "message": "viewer failed",
    }
    assert summary["world"] is None
    assert any("Performance final snapshot failure" in note for note in caught.value.__notes__)
    assert events[0]["event_type"] == "assignment"
    assert events[-1]["event_type"] == "stage_end"
    assert events[-1]["event_value"].endswith(":FAILURE:InjectedP4RunError")
    with runner.log_paths.events.open("a", encoding="utf-8") as event_file:
        event_file.write("")


def test_summary_persistence_failure_does_not_mask_viewer_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        replace(make_config(tmp_path, "run-summary-error"), camera_diagnostics=True),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with (
        pytest.raises(InjectedP4RunError, match="viewer failed") as caught,
        runner.session() as session,
    ):
        assert session.persistence is not None
        original_record = session.persistence.record_phase2_event

        def fail_performance_summary(
            event_type: str,
            payload_json: str,
            timestamp,
        ) -> None:
            if event_type == "performance_summary":
                raise InjectedSummaryPersistenceError("summary queue failed")
            original_record(event_type, payload_json, timestamp)

        monkeypatch.setattr(
            session.persistence,
            "record_phase2_event",
            fail_performance_summary,
        )
        viewer_factory.created[0].actions = [
            lambda: (_ for _ in ()).throw(InjectedP4RunError("viewer failed"))
        ]
        session.run()

    assert any(
        "InjectedSummaryPersistenceError:summary queue failed" in note
        for note in caught.value.__notes__
    )


def test_cleanup_only_failure_persists_partial_summary_and_raises_same_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    base_config = make_config(tmp_path, "run-shutdown-error")
    config = replace(
        base_config,
        camera_diagnostics=True,
        traffic_manager_port=8000,
        control_config=replace(
            base_config.control_config,
            longitudinal=replace(
                base_config.control_config.longitudinal,
                target_speed_kmh=None,
            ),
        ),
    )
    runner = ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )
    expected = InjectedShutdownError("deregistration failed")

    with pytest.raises(InjectedShutdownError) as caught, runner.session() as session:
        original_set_autopilot = vehicle.set_autopilot
        armed = False

        def fail_shutdown_once(enabled: bool, tm_port: int | None = None) -> None:
            nonlocal armed
            if armed and not enabled:
                armed = False
                raise expected
            original_set_autopilot(enabled, tm_port)

        def arm_shutdown_failure() -> None:
            nonlocal armed
            armed = True

        monkeypatch.setattr(vehicle, "set_autopilot", fail_shutdown_once)
        viewer_factory.created[0].actions = [arm_shutdown_failure]
        session.run()

    assert caught.value is expected
    assert runner.log_paths is not None
    events = read_dicts(runner.log_paths.events)
    summaries = [row for row in events if row["event_type"] == "performance_summary"]
    assert len(summaries) == 1
    payload = json.loads(summaries[0]["event_value"])
    assert payload["status"] == "PARTIAL"
    assert payload["error"] == {
        "type": "InjectedShutdownError",
        "message": "deregistration failed",
    }
