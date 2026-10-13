from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.p8.live_drive import run_live_drive
from tests.research_noa_fakes import FakeResearchVehicle
from tests.test_p8_live_drive import InteractiveViewerFactory, _runner, _services


class EventWriteFailure(OSError):
    pass


class InspectionFailure(OSError):
    pass


def test_live_adapter_closes_logger_and_writes_manifest_when_initial_emit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "initial-emit-failure")
    original_emit = services.emit

    def fail_initial_emit(name, payload=None, **kwargs):
        if name == "p8_dev_live_start_attempt":
            raise EventWriteFailure("start event failed")
        return original_emit(name, payload, **kwargs)

    monkeypatch.setattr(services, "emit", fail_initial_emit)

    result = run_live_drive(
        services,
        _runner(services, FakeResearchVehicle(), InteractiveViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert "start event failed" in result["abort_reason"]
    assert result["runtime_state"]["runtime_created"] is False
    assert services.closed is True
    assert (services.directory / "session_manifest.json").exists()


def test_live_adapter_keeps_primary_failure_when_abort_emit_also_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "abort-emit-failure")
    original_emit = services.emit

    def fail_abort_emit(name, payload=None, **kwargs):
        if name == "p8_dev_live_abort":
            raise EventWriteFailure("abort event failed")
        return original_emit(name, payload, **kwargs)

    monkeypatch.setattr(services, "emit", fail_abort_emit)

    result = run_live_drive(
        services,
        _runner(
            services,
            FakeResearchVehicle(reverse=True),
            InteractiveViewerFactory(),
        ),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert "ResearchNoATransmissionPrimeError" in result["abort_reason"]
    assert (
        "ABORT_EVENT_FAILURE:EventWriteFailure:abort event failed"
        in result["abort_reason"]
    )
    assert services.closed is True


def test_live_adapter_turns_inspection_failure_into_manifested_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "inspection-failure")

    def fail_inspection(*args, **kwargs):
        raise InspectionFailure("audit unavailable")

    monkeypatch.setattr("src.p8.live_drive.inspect_outputs", fail_inspection)

    result = run_live_drive(
        services,
        _runner(services, FakeResearchVehicle(), InteractiveViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert (
        "OUTPUT_INSPECTION_FAILURE:InspectionFailure:audit unavailable"
        in result["abort_reason"]
    )
    assert result["partial_artifacts"] == {
        "files": [],
        "issues": ["OUTPUT_INSPECTION_FAILURE:InspectionFailure:audit unavailable"],
        "software_artifacts_complete": False,
        "physical_completeness": "NOT_HARDWARE_VALIDATED",
    }
    saved = json.loads((services.directory / "session_manifest.json").read_text())
    assert saved["completion_status"] == "ABORTED"


def test_live_adapter_preserves_closed_runtime_when_end_event_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "end-event-failure")
    vehicle = FakeResearchVehicle()
    viewer_factory = InteractiveViewerFactory()
    original_emit = services.emit

    def fail_end_emit(name, payload=None, **kwargs):
        if name == "p8_dev_live_end":
            raise EventWriteFailure("end event failed")
        return original_emit(name, payload, **kwargs)

    monkeypatch.setattr(services, "emit", fail_end_emit)

    result = run_live_drive(
        services,
        _runner(services, vehicle, viewer_factory),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert result["abort_reason"] == "FAILURE:EventWriteFailure:end event failed"
    assert result["cleanup"]["runtime_owned_hero_and_viewer"] == "CLOSED"
    assert result["cleanup"]["logger_closed"] is True
    assert vehicle.destroy_count == 1
    assert viewer_factory.created[0].close_count == 1
