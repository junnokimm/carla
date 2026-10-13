from __future__ import annotations

import json
from pathlib import Path

import carla
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import DriverInput
from src.p8.live_drive import run_live_drive
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.test_p8_live_drive import _services
from tests.test_shutdown_brake_observability import (
    ShutdownPhaseError,
    StaleShutdownVehicle,
    _runner,
)


def test_manifest_retains_shutdown_observation_when_telemetry_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-telemetry-failure")
    original_write_telemetry = services.logger.write_telemetry
    vehicle = StaleShutdownVehicle([0.0, 0.0, 21.0, 7.0])
    vehicle.current_control = carla.VehicleControl(throttle=1.0, brake=0.0, gear=1)

    def fail_violation_telemetry(segment, timestamp, state) -> None:
        if state.speed_kmh == pytest.approx(7.0):
            raise OSError("violation telemetry unavailable")
        original_write_telemetry(segment, timestamp, state)

    monkeypatch.setattr(services.logger, "write_telemetry", fail_violation_telemetry)
    result = run_live_drive(
        services,
        _runner(services, vehicle),
        reported_history={},
    )

    saved = json.loads((services.directory / "session_manifest.json").read_text())
    observation = result["shutdown_brake"]["observation"]
    assert observation["capture_status"] == "CAPTURED_AFTER_COMMAND_CALL"
    assert observation["throttle"] == pytest.approx(1.0)
    assert observation["brake"] == pytest.approx(0.0)
    assert saved["shutdown_brake"] == result["shutdown_brake"]


def test_repeated_manual_failure_during_close_preserves_speed_error_and_cleanup(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-repeated-phase-error")
    vehicle = StaleShutdownVehicle([0.0, 0.0, 7.0])
    primary = ResearchNoAActualSpeedSafetyError(21.0, 20.0, source="NOA_ACTIVE")

    with (
        pytest.raises(ResearchNoAActualSpeedSafetyError) as caught,
        _runner(
            services,
            vehicle,
            DriverInput(activation_requested=True),
        ).session() as session,
    ):
        assert session.live_scheduler is not None
        request_control_mode = session.bundle.automation_runtime.request_control_mode

        def fail_manual_transition(mode: DrivingControlMode) -> None:
            if mode is DrivingControlMode.MANUAL:
                raise ShutdownPhaseError("manual transition failed")
            request_control_mode(mode)

        def fail_update() -> None:
            raise primary

        session.bundle.automation_runtime.request_control_mode = fail_manual_transition
        session.live_scheduler.update = fail_update
        session.run()

    assert caught.value is primary
    assert vehicle.destroy_count == 1
    services.close()


def test_manual_failure_during_close_without_primary_still_raises(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "shutdown-phase-error-without-primary")
    vehicle = StaleShutdownVehicle([])

    with (
        pytest.raises(ShutdownPhaseError),
        _runner(
            services,
            vehicle,
        ).session() as session,
    ):
        runtime = session.bundle.automation_runtime
        runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        request_control_mode = runtime.request_control_mode

        def fail_manual_transition(mode: DrivingControlMode) -> None:
            if mode is DrivingControlMode.MANUAL:
                raise ShutdownPhaseError("manual transition failed")
            request_control_mode(mode)

        runtime.request_control_mode = fail_manual_transition

    assert vehicle.destroy_count == 1
    services.close()
