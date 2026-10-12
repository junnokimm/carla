from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from math import isfinite
from pathlib import Path

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.experiment.automation_interaction_types import AutomationInteractionEventType
from src.experiment.context import ExperimentModule
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchWorld,
)
from tests.test_research_noa_nominal_policy import nominal_config
from tests.test_research_noa_nominal_runtime import HoldingSpeedVehicle


def _runner(
    tmp_path: Path,
    speeds: list[float],
    inputs: Iterator[DriverInput],
) -> tuple[ResearchNoARunner, FakeDriverViewFactory]:
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path),
        client=FakeClient(FakeResearchWorld(HoldingSpeedVehicle(speeds))),
        viewer_factory=viewer_factory,
        driver_input_source=inputs.__next__,
    )
    return runner, viewer_factory


@pytest.mark.parametrize("captured_speed_kmh", [0.0, 110.0])
def test_activation_snapshot_becomes_actual_control_target_and_logged_event(
    tmp_path: Path,
    captured_speed_kmh: float,
) -> None:
    runner, viewer_factory = _runner(
        tmp_path,
        [0.0, captured_speed_kmh],
        iter((DriverInput(activation_requested=True, stage="ACTIVATE"),)),
    )
    observed_targets: list[float | None] = []
    runner._automation_event_sink = lambda event: (
        observed_targets.append(runner_session.bundle.control_backend.target_speed_kmh)
        if event.event_type is AutomationInteractionEventType.TRANSITION
        and event.state.control_mode is DrivingControlMode.NOA_ACTIVE
        else None
    )

    with runner.session() as runner_session:
        viewer_factory.created[0].actions = [lambda: None]
        report = runner_session.run()

    assert report is not None
    assert report.target_speed_kmh == captured_speed_kmh
    assert report.commands[0].throttle == 0.0
    assert report.commands[0].brake == 0.0
    assert observed_targets == [captured_speed_kmh]
    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    activation = next(row for row in rows if row["event_type"] == "activation_speed_hold")
    payload = json.loads(activation["event_value"])
    assert payload["captured_speed_kmh"] == captured_speed_kmh
    assert payload["applied_target_speed_kmh"] == captured_speed_kmh
    assert payload["status"] == "APPLIED"
    assert "target_pending" not in payload
    assert "desired_speed_kmh" not in payload
    assert payload["activation_event_host_monotonic_ns"] == int(
        activation["host_monotonic_ns"]
    )


@pytest.mark.parametrize("captured_speed_kmh", [110.001, float("nan")])
def test_unsafe_activation_snapshot_logs_blocked_without_fake_target(
    tmp_path: Path,
    captured_speed_kmh: float,
) -> None:
    runner, viewer_factory = _runner(
        tmp_path,
        [0.0, captured_speed_kmh],
        iter((DriverInput(activation_requested=True),)),
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        with pytest.raises(ResearchNoAActualSpeedSafetyError):
            session.run()
        assert session.bundle.control_backend.target_speed_kmh is None

    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    activation = next(row for row in rows if row["event_type"] == "activation_speed_hold")
    payload = json.loads(activation["event_value"])
    assert payload["status"] == "BLOCKED"
    assert payload["captured_speed_kmh"] == (
        captured_speed_kmh if isfinite(captured_speed_kmh) else None
    )
    assert payload["applied_target_speed_kmh"] is None
    active_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "auto_state" and row["event_value"].endswith("NOA_ACTIVE")
    )
    capture_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "activation_speed_hold"
    )
    violation_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "speed_limit_violation"
    )
    assert active_index < capture_index < violation_index


def test_gate_rejection_does_not_capture_or_mutate_pending_target(tmp_path: Path) -> None:
    runner, viewer_factory = _runner(
        tmp_path,
        [0.0],
        iter((DriverInput(activation_requested=True, lane_change_in_progress=True),)),
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        report = session.run()
        assert report is not None
        assert session.bundle.control_backend.target_speed_kmh is None

    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as source:
        event_types = [row["event_type"] for row in csv.DictReader(source)]
    assert "activation_failure" in event_types
    assert "activation_speed_hold" not in event_types


def test_reactivation_recaptures_target_and_resets_same_pi_backend(tmp_path: Path) -> None:
    runner, viewer_factory = _runner(
        tmp_path,
        [0.0, 40.0, 40.0, 40.0, 50.0, 55.0, 55.0, 55.0],
        iter(
            (
                DriverInput(activation_requested=True),
                DriverInput(brake=0.2),
                DriverInput(activation_requested=True),
            )
        ),
    )

    with runner.session() as session:
        backend_id = id(session.bundle.control_backend)
        viewer_factory.created[0].actions = [lambda: None] * 3
        report = session.run()
        assert report is not None
        assert id(session.bundle.control_backend) == backend_id
        assert session.bundle.control_backend.target_speed_kmh == 55.0

    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as source:
        payloads = [
            json.loads(row["event_value"])
            for row in csv.DictReader(source)
            if row["event_type"] == "activation_speed_hold"
        ]
    assert [payload["applied_target_speed_kmh"] for payload in payloads] == [40.0, 55.0]


def test_module_two_initial_activation_holds_stationary_snapshot_without_launch(
    tmp_path: Path,
) -> None:
    vehicle = HoldingSpeedVehicle([0.0])
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path, ExperimentModule.MODULE_2),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        report = session.run()

    assert report is not None
    assert report.target_speed_kmh == 0.0
    assert report.commands[0].throttle == 0.0
    assert report.commands[0].brake == 0.0


def test_backend_transition_failure_has_no_activation_capture_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, viewer_factory = _runner(
        tmp_path,
        [0.0],
        iter((DriverInput(activation_requested=True),)),
    )

    with runner.session() as session:
        def fail_transition() -> None:
            raise RuntimeError("backend transition failed")

        monkeypatch.setattr(
            session.bundle.control_backend,
            "enter_noa_control",
            fail_transition,
        )
        viewer_factory.created[0].actions = [lambda: None]
        with pytest.raises(RuntimeError, match="backend transition failed"):
            session.run()

    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as source:
        event_types = [row["event_type"] for row in csv.DictReader(source)]
    assert "auto_request" in event_types
    assert "activation_speed_hold" not in event_types


def test_active_noa_keeps_captured_target_when_measured_speed_changes(tmp_path: Path) -> None:
    vehicle = HoldingSpeedVehicle([0.0])
    factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        nominal_config(tmp_path),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=factory,
        driver_input_source=iter((DriverInput(activation_requested=True), DriverInput())).__next__,
    )

    with runner.session() as session:
        pi_identity = id(session.bundle.control_backend._longitudinal_controller)
        factory.created[0].actions = [
            lambda: setattr(vehicle, "_last_speed", 40.0),
            lambda: setattr(vehicle, "_last_speed", 38.0),
        ]
        report = session.run()

        assert report is not None
        assert report.target_speed_kmh == 40.0
        assert report.commands[0].throttle == 0.0
        assert report.commands[1].throttle > 0.0
        assert id(session.bundle.control_backend._longitudinal_controller) == pi_identity
