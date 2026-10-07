from __future__ import annotations

import json

import pygame
import pytest

from src.experiment.automation import AutomationAvailability, DrivingControlMode
from src.vehicle.carla_noa_simulation_control import (
    SimulationTimeCarlaNoAControlBackend,
)
from src.vehicle.driving_mode import DrivingMode
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTimestamp,
    FakeWorldSnapshot,
)
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_noa_runtime import make_control_config


def test_runner_composes_owned_hero_map_bundle_and_scheduler() -> None:
    from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    control_clock = FakeMonotonicClock()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_clock=control_clock,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        assert session.hero is vehicle
        assert session.bundle.automation_runtime.state.availability is (
            AutomationAvailability.AVAILABLE
        )
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert session.bundle.control_backend.active is False
        assert isinstance(
            session.bundle.control_backend,
            SimulationTimeCarlaNoAControlBackend,
        )
        assert session.monotonic_clock is control_clock
        assert vehicle.autopilot_enabled is False
        assert viewer.config.initial_driving_mode is DrivingMode.MANUAL
        assert viewer.config.front_camera_only is False
        session.run()
        assert viewer.received_scheduler is session.dry_scheduler

    assert world.spawn_calls == [(world.blueprint, world.map.spawn_point)]
    assert world.blueprints.requests == ["vehicle.mercedes.coupe_2020"]
    assert world.blueprint.attributes == [("role_name", "research_noa")]
    assert vehicle.destroy_count == 1
    assert viewer_factory.created[0].close_count == 1


def test_dry_run_rejects_activation_and_never_applies_control() -> None:
    from src.scenario.research_noa import (
        ResearchNoADryRunActivationError,
        ResearchNoARunConfig,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        viewer.actions = [lambda: None, lambda: None, lambda: None]

        with pytest.raises(ResearchNoADryRunActivationError):
            session.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        session.run()

        assert vehicle.applied_controls == []
        assert vehicle.autopilot_calls == [False]
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert session.bundle.control_backend.active is False


def test_dry_cli_emits_one_camera_json_before_cleanup_without_control(
    monkeypatch,
    capsys,
) -> None:
    from src.scenario import research_noa
    from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner
    from tests.test_research_noa_cli import EXPLICIT_CONTROL_ARGUMENTS

    vehicle = FakeResearchVehicle()
    clock = FakeMonotonicClock()
    world = FakeResearchWorld(
        vehicle,
        monotonic_clock=clock,
        snapshots=(
            FakeWorldSnapshot(30, FakeTimestamp(10.0)),
            FakeWorldSnapshot(90, FakeTimestamp(30.0)),
        ),
    )
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_control_config(),
            camera_diagnostics=True,
            run_id="dry-a",
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
    )

    def serialize_while_open(self, measurements) -> str:
        assert self.close_count == 0
        return json.dumps(
            {
                "run_id": "dry-a",
                "run_measurements": {
                    "initial_world_frame": measurements.initial_world_frame,
                    "final_world_frame": measurements.final_world_frame,
                    "initial_simulation_seconds": measurements.initial_simulation_seconds,
                    "final_simulation_seconds": measurements.final_simulation_seconds,
                    "host_elapsed_seconds": measurements.host_elapsed_seconds,
                    "loop_count": measurements.loop_count,
                },
            },
            separators=(",", ":"),
        )

    monkeypatch.setattr(FakeDriverView, "camera_diagnostics_json", serialize_while_open)
    monkeypatch.setattr(research_noa, "ResearchNoARunner", lambda _config: runner)

    exit_code = research_noa.main(
        [
            "--dry-run",
            "--duration",
            "1",
            "--camera-diagnostics",
            "--run-id",
            "dry-a",
            *EXPLICIT_CONTROL_ARGUMENTS,
        ]
    )

    output = capsys.readouterr().out
    diagnostic_lines = [
        line
        for line in output.splitlines()
        if line.startswith("camera_diagnostics_json=")
    ]
    payload = json.loads(diagnostic_lines[0].partition("=")[2])
    assert exit_code == 0
    assert len(diagnostic_lines) == 1
    assert payload["run_measurements"] == {
        "initial_world_frame": 30,
        "final_world_frame": 90,
        "initial_simulation_seconds": 10.0,
        "final_simulation_seconds": 30.0,
        "host_elapsed_seconds": 0.0,
        "loop_count": 0,
    }
    assert vehicle.applied_controls == []
    assert viewer_factory.created[0].close_count == 1


def test_research_driver_view_ignores_legacy_p_key() -> None:
    from src.scenario.research_noa import ResearchDriverView, ResearchDriverViewConfig

    hero = FakeHero()
    viewer = ResearchDriverView(
        FakeWorld(),
        hero,
        ResearchDriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])

    assert viewer.driving_mode is DrivingMode.MANUAL
    assert hero.autopilot_calls == [False]
    assert hero.control_calls == []
