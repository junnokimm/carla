from __future__ import annotations

import csv
import json
from pathlib import Path

import carla
import pytest

from src.experiment.automation_interaction import DriverInput
from src.logging.research_event import ResearchEvent
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config, run_live_drive
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchWorld,
)
from tests.test_p8_live_drive import _services
from tests.test_research_noa_live_safety import SequencedResearchVehicle


class ShutdownBrakeRpcError(RuntimeError):
    pass


class ShutdownPhaseError(RuntimeError):
    pass


class StaleShutdownVehicle(SequencedResearchVehicle):
    def apply_control(self, control: carla.VehicleControl) -> None:
        if control.brake == pytest.approx(0.5) and not control.manual_gear_shift:
            self.events.append(f"control:{control.brake}")
            self.applied_controls.append(control)
            return
        super().apply_control(control)


class RaisingShutdownVehicle(SequencedResearchVehicle):
    def apply_control(self, control: carla.VehicleControl) -> None:
        if control.brake == pytest.approx(0.5) and not control.manual_gear_shift:
            self.events.append(f"control:{control.brake}")
            self.applied_controls.append(control)
            raise ShutdownBrakeRpcError("shutdown brake RPC failed")
        super().apply_control(control)


def _runner(
    services,
    vehicle: SequencedResearchVehicle,
    driver_input: DriverInput | None = None,
) -> ResearchNoARunner:
    viewer_factory = FakeDriverViewFactory()
    selected_driver_input = DriverInput() if driver_input is None else driver_input

    def configured_viewer_factory(world, hero, config):
        viewer = viewer_factory(world, hero, config)
        viewer.actions = [lambda: None]
        return viewer

    return ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=configured_viewer_factory,
        driver_input_source=iter((selected_driver_input,)).__next__,
        borrowed_log=services.borrowed_research_log(),
    )


def _violation_payload(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as source:
        event = next(
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "speed_limit_violation"
        )
    return json.loads(event["event_value"])


def test_returned_shutdown_call_with_stale_control_does_not_claim_braking(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-stale")
    vehicle = StaleShutdownVehicle([0.0, 0.0, 21.0, 7.0])
    vehicle.current_control = carla.VehicleControl(throttle=1.0, brake=0.0, gear=1)

    result = run_live_drive(services, _runner(services, vehicle), reported_history={})

    shutdown = result["shutdown_brake"]
    assert shutdown["command_call_outcome"] == "RETURNED"
    assert shutdown["physical_braking"] == "NOT_VERIFIED"
    assert shutdown["requested_control"] == {
        "throttle": 0.0,
        "brake": 0.5,
        "steer": 0.0,
    }
    assert shutdown["observation"]["throttle"] == pytest.approx(1.0)
    assert shutdown["observation"]["brake"] == pytest.approx(0.0)
    assert shutdown["observation"]["causal_application_proof"] is False
    assert shutdown["observation"]["frame_relation"] == "UNKNOWN"
    assert _violation_payload(services.logger.event_path)["shutdown_brake"] == shutdown


def test_raised_shutdown_call_keeps_speed_error_and_actor_cleanup(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-raised")
    vehicle = RaisingShutdownVehicle([0.0, 0.0, 21.0, 7.0])

    result = run_live_drive(services, _runner(services, vehicle), reported_history={})

    assert "ResearchNoAActualSpeedSafetyError" in result["abort_reason"]
    assert result["shutdown_brake"]["command_call_outcome"] == "RAISED"
    assert result["shutdown_brake"]["command_exception"] == {
        "type": "ShutdownBrakeRpcError",
        "message": "shutdown brake RPC failed",
    }
    assert result["shutdown_brake"]["physical_braking"] == "NOT_VERIFIED"
    assert vehicle.destroy_count == 1
    assert (
        _violation_payload(services.logger.event_path)["shutdown_brake"][
            "command_call_outcome"
        ]
        == "RAISED"
    )


def test_shutdown_apply_failure_preserves_primary_exception_object(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-primary-object")
    vehicle = RaisingShutdownVehicle([0.0, 0.0, 7.0])
    primary = ResearchNoAActualSpeedSafetyError(21.0, 20.0, source="NOA_ACTIVE")

    with _runner(services, vehicle).session() as session:
        assert session.live_scheduler is not None

        def fail_update():
            raise primary

        session.live_scheduler.update = fail_update
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value is primary
        assert session.shutdown_brake_outcome.command_call_outcome == "RAISED"

    assert vehicle.destroy_count == 1
    services.close()


def test_manual_transition_failure_is_not_labeled_as_apply_failure(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-phase-error")
    vehicle = SequencedResearchVehicle([])

    with _runner(services, vehicle).session() as session:
        request_control_mode = session.bundle.automation_runtime.request_control_mode

        def fail_manual_transition(_mode) -> None:
            raise ShutdownPhaseError("manual transition failed")

        session.bundle.automation_runtime.request_control_mode = fail_manual_transition
        try:
            with pytest.raises(ShutdownPhaseError):
                session._deactivate_live()
        finally:
            session.bundle.automation_runtime.request_control_mode = (
                request_control_mode
            )

        outcome = session.shutdown_brake_outcome
        assert outcome.command_call_outcome == "RETURNED"
        assert outcome.command_exception is None
        assert outcome.shutdown_phase_exception is not None
        assert outcome.shutdown_phase_exception.type == "ShutdownPhaseError"
        assert outcome.shutdown_phase_exception.message == "manual transition failed"

    assert vehicle.destroy_count == 1
    services.close()


def test_manifest_retains_shutdown_outcome_when_violation_event_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-event-failure")
    original_write_event = services.logger.write_event
    vehicle = StaleShutdownVehicle([0.0, 0.0, 21.0, 7.0])
    vehicle.current_control = carla.VehicleControl(throttle=1.0, brake=0.0, gear=1)

    def fail_violation_event(segment, timestamp, event: ResearchEvent) -> None:
        if event.event_type == "speed_limit_violation":
            raise OSError("violation event unavailable")
        original_write_event(segment, timestamp, event)

    monkeypatch.setattr(services.logger, "write_event", fail_violation_event)
    result = run_live_drive(
        services,
        _runner(services, vehicle),
        reported_history={},
    )

    saved = json.loads((services.directory / "session_manifest.json").read_text())
    assert result["shutdown_brake"]["command_call_outcome"] == "RETURNED"
    assert result["shutdown_brake"]["observation"]["capture_status"] == (
        "CAPTURED_AFTER_COMMAND_CALL"
    )
    assert result["shutdown_brake"]["observation"]["throttle"] == pytest.approx(1.0)
    assert result["shutdown_brake"]["observation"]["brake"] == pytest.approx(0.0)
    assert saved["shutdown_brake"] == result["shutdown_brake"]


def test_initial_violation_reports_shutdown_brake_not_attempted(tmp_path: Path) -> None:
    services = _services(tmp_path, "shutdown-not-attempted")
    vehicle = SequencedResearchVehicle([21.0, 7.0])

    result = run_live_drive(services, _runner(services, vehicle), reported_history={})

    assert result["shutdown_brake"]["command_call_outcome"] == "NOT_ATTEMPTED"
    assert result["shutdown_brake"]["command_exception"] is None
    assert result["shutdown_brake"]["physical_braking"] == "NOT_VERIFIED"
    assert (
        _violation_payload(services.logger.event_path)["shutdown_brake"]
        == result["shutdown_brake"]
    )


def test_normal_shutdown_exports_returned_call_without_physical_claim(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-normal")
    vehicle = SequencedResearchVehicle([0.0, 0.0, 5.0, 5.0, 5.0])

    result = run_live_drive(services, _runner(services, vehicle), reported_history={})

    shutdown = result["shutdown_brake"]
    assert result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert shutdown["command_call_outcome"] == "RETURNED"
    assert shutdown["command_started_host"] is not None
    assert shutdown["command_completed_host"] is not None
    assert shutdown["physical_braking"] == "NOT_VERIFIED"
    assert shutdown["observation"]["capture_status"] == "NOT_CAPTURED"
