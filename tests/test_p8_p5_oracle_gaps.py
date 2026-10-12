from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance import (
    ExitAssistanceStatus,
    ExitContext,
    ExitTerminationReason,
)
from src.p8.live_drive import run_live_drive
from src.p8.model import Mode, P8Error
from src.scenario.research_noa_config import ResearchNoAConfigError
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p5_boundary_contracts import event_payloads, observation, stamp
from tests.test_p8_live_drive import InteractiveViewerFactory, UserExitViewerFactory
from tests.test_p8_p5_live_drive import p5_runner, p5_services


class RuntimeFailureViewer(FakeDriverView):
    def run(self, duration, scheduler=None, diagnostics=None, input_observer=None):
        raise RuntimeError("driver loop failed")


class RuntimeFailureViewerFactory:
    def __call__(self, world, hero, config):
        return RuntimeFailureViewer(config)


def runner_with_viewer(
    services,
    world: FakeResearchWorld,
    viewer_factory,
) -> ResearchNoARunner:
    config = p5_runner(services, world).config
    return ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        exit_route_validator=lambda route, carla_map, hero: None,
        borrowed_log=services.borrowed_research_log(),
    )


def test_runtime_error_manifest_uses_terminal_p5_event_from_cleanup(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-error")
    world = FakeResearchWorld(FakeResearchVehicle())
    world.map.name = "Carla/Maps/Town04"

    result = run_live_drive(
        services,
        runner_with_viewer(services, world, RuntimeFailureViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "ABORTED"
    assert result["p5_exit"]["terminal_event"] == "lc_finished"
    assert result["p5_exit"]["coordinator_status"] == "INTERRUPTED"
    assert result["p5_exit"]["physical_outcome"] == "NOT_REACHED"
    assert result["p5_exit"]["termination_reason"] == "ERROR"
    assert result["p5_exit"]["noa_state_t0"] is None
    assert result["p5_exit"]["decision"] is None
    assert result["cleanup"]["runtime_owned_hero_and_viewer"] == "CLOSED"
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        terminal = next(
            row for row in csv.DictReader(source) if row["event_type"] == "lc_finished"
        )
    assert json.loads(terminal["event_value"])["termination_reason"] == "ERROR"


def test_escape_manifest_records_user_exit_terminal_and_cleanup(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "p8-p5-escape")
    world = FakeResearchWorld(FakeResearchVehicle())
    world.map.name = "Carla/Maps/Town04"

    result = run_live_drive(
        services,
        runner_with_viewer(services, world, UserExitViewerFactory()),
        reported_history={},
    )

    assert result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert result["runtime_report"]["user_exited"] is True
    assert result["p5_exit"]["terminal_event"] == "lc_finished"
    assert result["p5_exit"]["termination_reason"] == "USER_EXIT"
    assert result["cleanup"]["runtime_owned_hero_and_viewer"] == "CLOSED"


@pytest.mark.parametrize("action", ["confirm", "reject", "noresponse"])
def test_m1_on_before_t0_uses_actual_binding_and_records_decision(
    tmp_path: Path,
    action: str,
) -> None:
    services = p5_services(tmp_path, f"p8-p5-{action}")
    world = FakeResearchWorld(FakeResearchVehicle())
    with p5_runner(services, world).session() as runtime:
        assert runtime.exit_assistance is not None
        assert runtime.exit_view_binding is not None
        assert runtime.interaction is not None
        interaction_config = runtime.config.automation_interaction
        assert interaction_config is not None
        runtime.interaction.initialize(
            interaction_config.module,
            interaction_config.condition,
        )
        runtime.interaction.update(DriverInput(activation_requested=True))
        assert runtime.interaction.state.control_mode is DrivingControlMode.NOA_ACTIVE
        route = runtime.exit_assistance.config.route
        runtime.exit_assistance.observe(
            replace(
                observation(route.confirmation_start, -3.5, frame=1),
                timestamp=runtime.persistence.source.get_host_timestamp(),
            )
        )
        rendered = runtime.exit_view_binding.prepare_presentation()
        assert rendered is not None
        assert rendered.recommendation is True
        runtime.exit_view_binding.mark_presented(rendered)
        presented = event_payloads(runtime.exit_assistance)[-1]
        t0 = presented["t0_host_monotonic_ns"]
        assert isinstance(t0, int)
        driver_input = DriverInput(
            exit_confirm_requested=action == "confirm",
            exit_reject_requested=action == "reject",
        )
        runtime.exit_assistance.update(
            driver_input,
            runtime.interaction.state,
            stamp(t0 + (6_000_000_000 if action == "noresponse" else 1)),
        )
        expected = action.upper() if action != "noresponse" else "NORESPONSE"
        assert event_payloads(runtime.exit_assistance)[-1]["decision"] == expected
        if action == "noresponse":
            runtime.exit_assistance.finish(
                ExitTerminationReason.TIME_CAP,
                stamp(t0 + 6_000_000_001),
            )
        runtime.persistence.flush_events()
        assert runtime.persistence.logger is services.logger
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        exit_rows = [
            row for row in csv.DictReader(source) if row["event_type"].startswith("lc_")
        ]
    assert exit_rows
    assert {json.loads(row["event_value"])["lc_event_id"] for row in exit_rows} == {
        f"p8-p5-{action}:MODULE_1:town04-exit-39"
    }
    services.close()


def test_m1_off_t0_then_n_interrupts_instead_of_retroactive_assistance(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-off-t0")
    world = FakeResearchWorld(FakeResearchVehicle())
    with p5_runner(services, world).session() as runtime:
        assert runtime.exit_assistance is not None
        assert runtime.exit_view_binding is not None
        assert runtime.interaction is not None
        interaction_config = runtime.config.automation_interaction
        assert interaction_config is not None
        runtime.interaction.initialize(
            interaction_config.module,
            interaction_config.condition,
        )
        route = runtime.exit_assistance.config.route
        runtime.exit_assistance.observe(
            replace(
                observation(route.confirmation_start, -3.5, frame=1),
                timestamp=runtime.persistence.source.get_host_timestamp(),
            )
        )
        rendered = runtime.exit_view_binding.prepare_presentation()
        assert rendered is not None
        assert rendered.recommendation is False
        runtime.exit_view_binding.mark_presented(rendered)
        assert event_payloads(runtime.exit_assistance)[-1]["noa_state_t0"] == "MANUAL"

        runtime.interaction.update(DriverInput(activation_requested=True))

        assert runtime.exit_assistance.status is ExitAssistanceStatus.INTERRUPTED
        terminal = event_payloads(runtime.exit_assistance)[-1]
        assert terminal["termination_reason"] == "MODE_CHANGED_AFTER_T0"
        assert terminal["noa_state_t0"] == "MANUAL"
    services.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda config: replace(
            config,
            exit_assistance=replace(
                config.exit_assistance, context=ExitContext.TRAINING
            ),
        ),
        lambda config: replace(
            config,
            automation_interaction=replace(config.automation_interaction, noa_key="q"),
        ),
        lambda config: replace(
            config,
            exit_assistance=replace(config.exit_assistance, confirm_key="f"),
        ),
        lambda config: replace(
            config,
            persistence=replace(config.persistence, stage_label="other-stage"),
        ),
    ],
)
def test_p5_gate_rejects_semantically_wrong_binding_before_spawn(
    tmp_path: Path,
    mutate,
) -> None:
    services = p5_services(tmp_path, "p8-p5-semantic-gate")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    mismatched = ResearchNoARunner(
        mutate(runner.config),
        client=FakeClient(world),
        viewer_factory=InteractiveViewerFactory(),
        exit_route_validator=lambda route, carla_map, hero: None,
        borrowed_log=services.borrowed_research_log(),
    )

    with pytest.raises(P8Error, match="P8_LIVE_RUNNER_CONFIG_MISMATCH"):
        run_live_drive(services, mismatched, reported_history={})

    assert world.spawn_calls == []
    services.close()


def test_injected_viewer_is_not_labeled_real_keyboard(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "p8-p5-provenance")
    config = p5_runner(services, FakeResearchWorld(FakeResearchVehicle())).config

    real = ResearchNoARunner(config, borrowed_log=services.borrowed_research_log())
    injected = ResearchNoARunner(
        config,
        viewer_factory=InteractiveViewerFactory(),
        borrowed_log=services.borrowed_research_log(),
    )

    assert real.driver_input_source == "REAL_KEYBOARD_PATH"
    assert injected.driver_input_source == "INJECTED_TEST_VIEWER"
    services.close()


def test_p8_p5_keeps_participant_and_duration_and_input_guards(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "p8-p5-policy")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world, duration=120.0, development_validation=True)
    assert runner.config.duration == 120.0
    with pytest.raises(ResearchNoAConfigError, match="duration"):
        replace(runner.config, duration=120.001)
    assert runner.config.automation_interaction is not None
    with pytest.raises(ResearchNoAConfigError, match="conflicts"):
        replace(
            runner.config,
            automation_interaction=replace(
                runner.config.automation_interaction,
                noa_key="c",
            ),
        )
    services.mode = Mode.PARTICIPANT
    with pytest.raises(P8Error, match="P8_LIVE_MODE_REQUIRED"):
        run_live_drive(services, runner, reported_history={})
    assert world.spawn_calls == []
    services.close()
