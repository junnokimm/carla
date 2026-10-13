from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.p8.assignment import development_assignment
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config, run_live_drive
from src.p8.model import Mode
from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tools.p8_smoke import main


class InteractiveViewerFactory(FakeDriverViewFactory):
    def __call__(self, world, hero, config):
        viewer = super().__call__(world, hero, config)
        viewer.actions = [lambda: None, lambda: None, lambda: None, lambda: None]
        return viewer


class UserExitViewer(FakeDriverView):
    def run(self, duration, scheduler=None, diagnostics=None, input_observer=None):
        super().run(duration, scheduler, diagnostics, input_observer)
        return True


class UserExitViewerFactory:
    def __init__(self) -> None:
        self.created: list[UserExitViewer] = []

    def __call__(self, world, hero, config):
        viewer = UserExitViewer(config)
        viewer.actions = [lambda: None, lambda: None, lambda: None, lambda: None]
        self.created.append(viewer)
        return viewer


def _services(root: Path, run_id: str) -> SessionServices:
    return SessionServices(
        root,
        development_assignment(),
        run_id,
        Mode.DEV_LIVE_DRIVE,
    )


def _runner(
    services: SessionServices,
    vehicle: FakeResearchVehicle,
    viewer_factory,
) -> ResearchNoARunner:
    inputs = iter(
        (
            DriverInput(activation_requested=True),
            DriverInput(brake=0.2),
            DriverInput(),
            DriverInput(activation_requested=True),
        )
    )
    return ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0, front_camera_only=True),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        driver_input_source=inputs.__next__,
        borrowed_log=services.borrowed_research_log(),
    )


def test_borrowed_runtime_uses_exact_p8_context_clock_and_writer(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "identity")
    vehicle = FakeResearchVehicle()
    viewer_factory = InteractiveViewerFactory()
    runner = _runner(services, vehicle, viewer_factory)

    with runner.session() as session:
        assert session.persistence is not None
        assert session.persistence.study_run is services.context
        assert session.persistence.logger is services.logger
        assert services.logger._session_clock is services.clock
        report = session.run()

    assert report is not None
    assert report.control_frames >= 2
    assert services.closed is False
    assert len(viewer_factory.created) == 1
    assert viewer_factory.created[0].close_count == 1
    assert vehicle.destroy_count == 1
    services.close()


def test_live_adapter_records_real_partial_run_without_disabled_streams(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "partial")
    vehicle = FakeResearchVehicle()

    result = run_live_drive(
        services,
        _runner(services, vehicle, InteractiveViewerFactory()),
        reported_history={"p7": "LIVE_NOT_TESTED"},
    )

    assert result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert result["participant_run"] is False
    assert result["participant_ready"] is False
    assert result["module_1_completed"] is False
    assert result["runtime_state"] == {
        "runtime_created": True,
        "stage_started": True,
    }
    assert result["cleanup"] == {
        "logger_closed": True,
        "logger_status": "CLOSED",
        "runtime_owned_hero_and_viewer": "CLOSED",
        "camera_sensor_destruction": "UNVERIFIED",
        "external_ego_destroyed": False,
    }
    assert result["sources"] == {
        "carla": "INJECTED_TEST_CLIENT",
        "driver_input": "INJECTED_TEST_DRIVER_INPUT",
        "telemetry": "RUNTIME_CARLA_SNAPSHOT_ADAPTER",
        "p5_exit": "DISABLED",
        "p6_tablet": "DISABLED",
        "p6_gaze": "DISABLED",
        "p7_hazard": "DISABLED",
        "survey": "DISABLED",
    }
    assert services.closed is True
    manifest = json.loads((services.directory / "session_manifest.json").read_text())
    assert manifest["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert manifest["shared_objects"] == {
        "study_context": True,
        "session_clock": True,
        "research_csv_logger": True,
    }
    with services.logger.event_path.open(newline="") as source:
        events = list(csv.DictReader(source))
    event_types = [row["event_type"] for row in events]
    assert "stage_start_attempt" in event_types
    assert "stage_start" in event_types
    assert "stage_end" in event_types
    assert "stage_abort" not in event_types
    assert event_types.count("auto_request") == 2
    assert "disengagement" in event_types
    with services.logger.telemetry_path.open(newline="") as source:
        telemetry = list(csv.DictReader(source))
    assert telemetry
    assert all(row["carla_frame"] for row in telemetry)
    assert all(row["speed_kmh"] == "0.0" for row in telemetry)
    assert "REAL_CARLA_RUNTIME_DEV_PARTIAL" not in "".join(
        row["event_value"] for row in events
    )
    assert all(
        item["provenance"] == "DEV_STUB"
        for item in result["partial_artifacts"]["files"]
    )


def test_live_adapter_records_user_exit_separately_from_time_cap(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "user-exit")

    result = run_live_drive(
        services,
        _runner(services, FakeResearchVehicle(), UserExitViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert result["runtime_report"]["user_exited"] is True
    with services.logger.event_path.open(newline="") as source:
        events = list(csv.DictReader(source))
    end = next(row for row in events if row["event_type"] == "p8_dev_live_end")
    assert json.loads(end["event_value"])["reason"] == "USER_EXIT"


def test_dev_live_cli_requires_explicit_ack_before_network_access(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--mode",
                "dev-live-drive",
                "--spawn-index",
                "0",
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
