from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.p8.assignment import development_assignment
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config, run_live_drive
from src.p8.model import Mode, P8Error, Stage
from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import FakeClient, FakeResearchVehicle, FakeResearchWorld
from tests.test_p8_live_drive import InteractiveViewerFactory, _services


@pytest.mark.parametrize(
    ("mode", "stage", "message"),
    [
        (Mode.DRY, Stage.SESSION_CREATED, "P8_LIVE_MODE_REQUIRED"),
        (Mode.DEV_LIVE_DRIVE, Stage.TRAINING, "P8_LIVE_SESSION_STAGE_REQUIRED"),
    ],
)
def test_live_adapter_rejects_wrong_entry_gate_before_runtime_creation(
    tmp_path: Path,
    mode: Mode,
    stage: Stage,
    message: str,
) -> None:
    services = SessionServices(
        tmp_path,
        development_assignment(),
        f"gate-{mode.value}-{stage.value}",
        mode,
    )
    services.stage = stage
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        borrowed_log=services.borrowed_research_log(),
    )

    with pytest.raises(P8Error, match=message):
        run_live_drive(services, runner, reported_history={})

    assert world.spawn_calls == []
    assert services.stage is stage
    services.close()


def test_live_adapter_rejects_borrowed_logger_mismatch_before_spawn(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "binding-primary")
    other = _services(tmp_path, "binding-other")
    world = FakeResearchWorld(FakeResearchVehicle())
    binding = replace(services.borrowed_research_log(), logger=other.logger)
    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        borrowed_log=binding,
    )

    with pytest.raises(P8Error, match="P8_LIVE_SHARED_RESOURCE_IDENTITY_MISMATCH"):
        run_live_drive(services, runner, reported_history={})

    assert world.spawn_calls == []
    services.close()
    other.close()


def test_live_adapter_rejects_runner_config_mismatch_before_spawn(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "config-mismatch")
    world = FakeResearchWorld(FakeResearchVehicle())
    config = build_live_drive_config(
        services,
        LiveDriveOptions(duration=1.0, spawn_index=0),
    )
    assert config.persistence is not None
    config = replace(
        config,
        persistence=replace(config.persistence, output_dir=tmp_path / "other"),
    )
    runner = ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        borrowed_log=services.borrowed_research_log(),
    )

    with pytest.raises(P8Error, match="P8_LIVE_RUNNER_CONFIG_MISMATCH"):
        run_live_drive(services, runner, reported_history={})

    assert world.spawn_calls == []
    services.close()


def test_live_adapter_rejects_synchronous_world_before_spawn(tmp_path: Path) -> None:
    services = _services(tmp_path, "synchronous-world")
    world = FakeResearchWorld(FakeResearchVehicle(), synchronous_mode=True)
    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        borrowed_log=services.borrowed_research_log(),
    )

    result = run_live_drive(services, runner, reported_history={})

    assert result["completion_status"] == "ABORTED"
    assert "synchronous" in result["abort_reason"].lower()
    assert world.spawn_calls == []
