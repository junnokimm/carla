from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation import DrivingControlMode
from src.p8.assignment import development_assignment
from src.p8.live_drive import (
    LiveDriveOptions,
    build_live_drive_config,
    run_live_drive,
)
from src.p8.model import Mode, P8Error
from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.p8_p5_spawn_fakes import configure_p5_spawn
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p8_live_drive import InteractiveViewerFactory
from tools.p8_smoke import main

ROUTE_MANIFEST = Path("config/town04_exit_routes_dev_v3.json")
ROUTE_ID = "town04-exit-39"


def p5_services(tmp_path: Path, run_id: str) -> SessionServices:
    assignment = replace(development_assignment(), route_id=ROUTE_ID)
    return SessionServices(tmp_path, assignment, run_id, Mode.DEV_LIVE_DRIVE)


def p5_runner(
    services: SessionServices,
    world: FakeResearchWorld,
    *,
    duration: float = 1.0,
    development_validation: bool = False,
) -> ResearchNoARunner:
    config = build_live_drive_config(
        services,
        LiveDriveOptions(
            duration=duration,
            spawn_index=0,
            exit_manifest=ROUTE_MANIFEST,
            exit_route_id=ROUTE_ID,
            development_validation=development_validation,
        ),
    )
    configure_p5_spawn(world, config)
    return ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        exit_route_validator=lambda route, carla_map, hero: None,
        borrowed_log=services.borrowed_research_log(),
    )


def test_p8_builds_run_scoped_actual_p5_config_with_m1_initially_off(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-config")

    config = p5_runner(
        services,
        FakeResearchWorld(FakeResearchVehicle()),
    ).config

    assert config.exit_assistance is not None
    assert config.exit_assistance.lc_event_id == "p8-p5-config:MODULE_1:town04-exit-39"
    assert config.exit_assistance.route_id == services.assignment.route_id
    with p5_runner(
        services,
        FakeResearchWorld(FakeResearchVehicle()),
    ).session() as runtime:
        assert runtime.exit_assistance is not None
        assert runtime.exit_view_binding is not None
        assert (
            runtime.bundle.automation_runtime.state.control_mode
            is DrivingControlMode.MANUAL
        )
    services.close()


def test_p8_actual_p5_runtime_records_binding_event_and_physical_outcome(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-runtime")
    world = FakeResearchWorld(FakeResearchVehicle())

    result = run_live_drive(
        services,
        p5_runner(services, world),
        reported_history={},
    )

    assert result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert result["sources"]["p5_exit"] == "ACTUAL_P5_CONFIGURED"
    assert result["p5_exit"] == {
        "configured": True,
        "runtime_binding": "BOUND",
        "manifest_path": str(ROUTE_MANIFEST),
        "route_id": ROUTE_ID,
        "lc_event_id": "p8-p5-runtime:MODULE_1:town04-exit-39",
        "development_validation": False,
        "coordinator_status": "INTERRUPTED",
        "terminal_event": "lc_finished",
        "physical_outcome": "INTERRUPTED",
        "termination_reason": "TIME_CAP",
        "noa_state_t0": None,
        "decision": None,
    }
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    exit_manifest = next(row for row in rows if row["event_type"] == "p5_exit_manifest")
    assert (
        json.loads(exit_manifest["event_value"])["lc_event_id"]
        == result["p5_exit"]["lc_event_id"]
    )
    finished = next(row for row in rows if row["event_type"] == "lc_finished")
    assert json.loads(finished["event_value"])["noa_state_t0"] is None


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda config: replace(
                config,
                exit_assistance=replace(
                    config.exit_assistance, route_id="town04-exit-47"
                ),
            ),
            "P8_LIVE_RUNNER_CONFIG_MISMATCH",
        ),
    ],
)
def test_p8_p5_gate_rejects_configuration_or_assignment_mismatch_before_spawn(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    services = p5_services(tmp_path, "p8-p5-gate")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    runner = ResearchNoARunner(
        mutate(runner.config),
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        exit_route_validator=lambda route, carla_map, hero: None,
        borrowed_log=services.borrowed_research_log(),
    )

    with pytest.raises(P8Error, match=message):
        run_live_drive(services, runner, reported_history={})

    assert world.spawn_calls == []
    services.close()


def test_p8_p5_options_reject_partial_configuration() -> None:
    with pytest.raises(P8Error, match="P8_LIVE_P5_MANIFEST_ROUTE_REQUIRED_TOGETHER"):
        LiveDriveOptions(
            duration=1.0,
            spawn_index=0,
            exit_manifest=ROUTE_MANIFEST,
        )


def test_p8_p5_map_mismatch_fails_before_spawn_and_is_manifested(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-map")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    world.map.name = "Carla/Maps/Town03"

    result = run_live_drive(services, runner, reported_history={})

    assert result["completion_status"] == "ABORTED"
    assert "does not match" in result["abort_reason"]
    assert world.spawn_calls == []
    assert result["p5_exit"]["runtime_binding"] == "NOT_CREATED"


def test_p8_cli_requires_explicit_p5_pair_and_dev_validation_opt_in(
    tmp_path: Path,
) -> None:
    invalid = [
        ["--exit-route-manifest", str(ROUTE_MANIFEST)],
        ["--exit-route-id", ROUTE_ID],
        ["--development-validation"],
        ["--duration", "61"],
    ]
    for extra in invalid:
        with pytest.raises(SystemExit) as caught:
            main(
                [
                    "--mode",
                    "dev-live-drive",
                    "--ack-dev-live",
                    "--spawn-index",
                    "0",
                    *extra,
                    "--output-root",
                    str(tmp_path),
                ]
            )
        assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("development_validation", [False, True])
def test_p8_p5_rejects_high_speed_dev_policy_before_services(
    tmp_path: Path,
    development_validation: bool,
) -> None:
    arguments = [
        "--mode",
        "dev-live-drive",
        "--ack-dev-live",
        "--spawn-index",
        "0",
        "--noa-desired-speed-kmh",
        "25",
        "--dev-speed-limit-kmh",
        "35",
        "--ack-high-speed-dev",
        "--noa-max-throttle",
        "1",
        "--exit-route-manifest",
        str(ROUTE_MANIFEST),
        "--exit-route-id",
        ROUTE_ID,
        "--output-root",
        str(tmp_path),
    ]
    if development_validation:
        arguments.append("--development-validation")

    with pytest.raises(SystemExit) as caught:
        main(arguments)

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


def test_p8_cli_allows_bounded_long_p5_development_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class LongDurationViewer(FakeDriverView):
        def run(self, duration, scheduler=None, diagnostics=None, input_observer=None):
            return super().run(1.0, scheduler, diagnostics, input_observer)

    class LongDurationViewerFactory:
        def __call__(self, world, hero, config):
            viewer = LongDurationViewer(config)
            viewer.actions = [lambda: None, lambda: None, lambda: None, lambda: None]
            return viewer

    def fake_runner(config, *, borrowed_log):
        world = FakeResearchWorld(FakeResearchVehicle())
        configure_p5_spawn(world, config)
        return ResearchNoARunner(
            config,
            client=FakeClient(world),
            viewer_factory=LongDurationViewerFactory(),
            exit_route_validator=lambda route, carla_map, hero: None,
            borrowed_log=borrowed_log,
        )

    monkeypatch.setattr(
        "src.scenario.research_noa_runtime.ResearchNoARunner",
        fake_runner,
    )

    exit_code = main(
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--duration",
            "21",
            "--exit-route-manifest",
            str(ROUTE_MANIFEST),
            "--exit-route-id",
            ROUTE_ID,
            "--development-validation",
            "--output-root",
            str(tmp_path),
        ]
    )

    manifest = json.loads(next(tmp_path.glob("*/session_manifest.json")).read_text())
    assert exit_code == 0
    assert manifest["p5_exit"]["development_validation"] is True
    assert manifest["runtime_report"]["requested_duration_s"] == 21.0


def test_session_manifest_refuses_overwrite(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "manifest-once")
    services.save_manifest_once({"first": True})

    with pytest.raises(P8Error, match="SESSION_MANIFEST_EXISTS"):
        services.save_manifest_once({"first": False})

    assert json.loads((services.directory / "session_manifest.json").read_text()) == {
        "first": True
    }
    services.close()
