from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.p8.live_drive import run_live_drive
from tests.research_noa_fakes import FakeDriverView, FakeResearchVehicle
from tests.test_p8_live_drive import (
    InteractiveViewerFactory,
    _runner,
    _services,
)


class AttachFailure(RuntimeError):
    pass


class SessionCloseFailure(RuntimeError):
    pass


class LoggerCloseFailure(RuntimeError):
    pass


class FailingAttachViewer(FakeDriverView):
    def attach(self) -> None:
        raise AttachFailure("camera attach failed")


class FailingAttachViewerFactory:
    def __call__(self, world, hero, config):
        return FailingAttachViewer(config)


class FailingCloseViewer(FakeDriverView):
    def close(self) -> None:
        super().close()
        raise SessionCloseFailure("viewer cleanup failed")


class FailingCloseViewerFactory:
    def __call__(self, world, hero, config):
        viewer = FailingCloseViewer(config)
        viewer.actions = [lambda: None, lambda: None, lambda: None, lambda: None]
        return viewer


def test_live_adapter_preserves_partial_files_when_viewer_creation_fails(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "creation-failure")
    vehicle = FakeResearchVehicle()

    result = run_live_drive(
        services,
        _runner(services, vehicle, FailingAttachViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert result["abort_reason"] == "FAILURE:AttachFailure:camera attach failed"
    assert result["runtime_state"] == {
        "runtime_created": False,
        "stage_started": False,
    }
    assert result["cleanup"]["runtime_owned_hero_and_viewer"] == "UNKNOWN"
    assert vehicle.destroy_count == 1
    assert services.closed is True
    assert services.logger.event_path.exists()
    assert services.logger.telemetry_path.exists()
    assert (services.directory / "session_manifest.json").exists()


def test_live_adapter_cannot_report_success_when_session_close_fails(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "session-close-failure")
    vehicle = FakeResearchVehicle()

    result = run_live_drive(
        services,
        _runner(services, vehicle, FailingCloseViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert result["abort_reason"] == (
        "FAILURE:SessionCloseFailure:viewer cleanup failed"
    )
    assert result["runtime_state"] == {
        "runtime_created": True,
        "stage_started": True,
    }
    assert result["cleanup"]["runtime_owned_hero_and_viewer"] == "FAILED"
    assert vehicle.destroy_count == 1
    with services.logger.event_path.open(newline="") as source:
        event_types = [row["event_type"] for row in csv.DictReader(source)]
    assert "p8_dev_live_abort" in event_types
    assert "p8_dev_live_end" not in event_types


def test_live_adapter_preserves_manifest_when_logger_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "logger-close-failure")
    original_close = services.logger.close

    def fail_after_close() -> None:
        original_close()
        raise LoggerCloseFailure("research CSV close failed")

    monkeypatch.setattr(services.logger, "close", fail_after_close)

    result = run_live_drive(
        services,
        _runner(services, FakeResearchVehicle(), InteractiveViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert result["abort_reason"] == (
        "LOGGER_CLOSE_FAILURE:LoggerCloseFailure:research CSV close failed"
    )
    assert result["cleanup"]["logger_closed"] is False
    assert result["cleanup"]["logger_status"] == "FAILED"
    manifest = json.loads((services.directory / "session_manifest.json").read_text())
    assert manifest["completion_status"] == "ABORTED"


def test_live_adapter_does_not_claim_stage_started_when_transmission_fails(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "transmission-failure")

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
    assert result["runtime_state"] == {
        "runtime_created": True,
        "stage_started": False,
    }
    with services.logger.event_path.open(newline="") as source:
        event_types = [row["event_type"] for row in csv.DictReader(source)]
    assert "p8_dev_live_runtime_created" in event_types
    assert "p8_dev_live_start" not in event_types
    assert "stage_start" not in event_types
