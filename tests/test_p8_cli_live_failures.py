from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import FakeClient, FakeResearchVehicle, FakeResearchWorld
from tests.test_p8_live_drive import InteractiveViewerFactory
from tools.p8_smoke import main


class AssignmentEventFailure(OSError):
    pass


def test_dev_live_cli_manifests_one_shot_assignment_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    created: list[SessionServices] = []

    def services_factory(*args, **kwargs):
        services = SessionServices(*args, **kwargs)
        original_emit = services.emit

        def fail_assignment_once(name, payload=None, **emit_kwargs):
            if name == "p8_assignment_provenance":
                services.emit = original_emit
                raise AssignmentEventFailure("assignment event failed")
            return original_emit(name, payload, **emit_kwargs)

        services.emit = fail_assignment_once
        created.append(services)
        return services

    def runner_factory(config, *, borrowed_log):
        return ResearchNoARunner(
            config,
            client=FakeClient(world),
            viewer_factory=InteractiveViewerFactory(),
            borrowed_log=borrowed_log,
        )

    monkeypatch.setattr("tools.p8_smoke.SessionServices", services_factory)
    monkeypatch.setattr(
        "src.scenario.research_noa_runtime.ResearchNoARunner",
        runner_factory,
    )

    exit_code = main(
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--duration",
            "1",
            "--output-root",
            str(tmp_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    manifest = json.loads((created[0].directory / "session_manifest.json").read_text())
    assert exit_code == 2
    assert output["status"] == "ABORTED"
    assert manifest["completion_status"] == "ABORTED"
    assert manifest["abort_reason"] == (
        "FAILURE:AssignmentEventFailure:assignment event failed"
    )
    assert created[0].closed is True
    assert world.spawn_calls == []
    assert vehicle.destroy_count == 0
