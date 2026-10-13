from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.context import ExperimentModule, ExperimentPhase
from src.experiment.exit_assistance import ExitContext
from src.p6.config import P6Config
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner
from src.scenario.research_noa_config import ResearchNoAConfigError
from src.scenario.research_noa_speed_policy import ResearchSpeedPolicy
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p4_research_noa_integration import make_config
from tests.test_research_noa_nominal_policy import nominal_config

ROUTE_MANIFEST = Path("config/town04_exit_routes_dev_v3.json")
ROUTE_ID = "town04-exit-39"


def operator_p5_nominal_config(
    output_dir: Path,
    module: ExperimentModule = ExperimentModule.MODULE_1,
) -> ResearchNoARunConfig:
    base = nominal_config(output_dir, module)
    assert base.persistence is not None
    assignment = replace(base.persistence.assignment, route_id=ROUTE_ID)
    return replace(
        base,
        persistence=replace(base.persistence, assignment=assignment),
        exit_assistance=ResearchExitAssistanceConfig(
            ROUTE_MANIFEST,
            ROUTE_ID,
            f"nominal:{module.value}:exit",
            context=ExitContext.MODULE,
            eligibility_open_s=0.0,
            eligibility_close_s=duration_for_module(module),
        ),
    )


def duration_for_module(module: ExperimentModule) -> float:
    return 600.0 if module is ExperimentModule.MODULE_1 else 780.0


def operator_p5_training_config(
    output_dir: Path,
    *,
    duration: float = 600.0,
) -> ResearchNoARunConfig:
    base = nominal_config(output_dir)
    assert base.persistence is not None
    assert base.automation_interaction is not None
    assignment = replace(base.persistence.assignment, route_id="study-training-route")
    return replace(
        base,
        duration=duration,
        operator_nominal_module=False,
        operator_training=True,
        persistence=replace(
            base.persistence,
            assignment=assignment,
            stage_label=ExperimentPhase.TRAINING.value,
            segment_phase=ExperimentPhase.TRAINING,
        ),
        automation_interaction=replace(
            base.automation_interaction,
            initial_stage=ExperimentPhase.TRAINING.value,
        ),
        exit_assistance=ResearchExitAssistanceConfig(
            ROUTE_MANIFEST,
            ROUTE_ID,
            "training:MODULE_1:exit",
            context=ExitContext.TRAINING,
        ),
    )


@pytest.mark.parametrize(
    ("module", "duration"),
    [
        (ExperimentModule.MODULE_1, 600.0),
        (ExperimentModule.MODULE_2, 780.0),
    ],
)
def test_operator_p5_nominal_accepts_exact_assigned_module_context(
    tmp_path: Path,
    module: ExperimentModule,
    duration: float,
) -> None:
    config = operator_p5_nominal_config(tmp_path, module)

    assert (config.duration, config.activation_speed_hold) == (duration, True)
    assert config.exit_assistance is not None
    assert config.exit_assistance.context is ExitContext.MODULE
    assert config.persistence is not None
    assert config.exit_assistance.route_id == config.persistence.assignment.route_id


@pytest.mark.parametrize("duration", [1.0, 600.0])
def test_operator_p5_training_accepts_explicit_training_route_through_600_seconds(
    tmp_path: Path,
    duration: float,
) -> None:
    config = operator_p5_training_config(tmp_path, duration=duration)

    assert (config.operator_training, config.duration) == (True, duration)
    assert config.activation_speed_hold is True
    assert config.exit_assistance is not None
    assert config.exit_assistance.context is ExitContext.TRAINING
    assert config.persistence is not None
    assert config.exit_assistance.route_id != config.persistence.assignment.route_id


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"activation_speed_hold": False},
        {"p6": P6Config(1, "synthetic")},
        {"development_validation": True},
        {
            "exit_assistance": ResearchExitAssistanceConfig(
                ROUTE_MANIFEST,
                "different-route",
                "nominal:wrong-route",
                context=ExitContext.MODULE,
            )
        },
        {
            "exit_assistance": ResearchExitAssistanceConfig(
                ROUTE_MANIFEST,
                ROUTE_ID,
                "nominal:wrong-context",
                context=ExitContext.TRAINING,
            )
        },
    ],
)
def test_operator_p5_nominal_rejects_partial_or_mismatched_context(
    tmp_path: Path,
    invalid_update,
) -> None:
    with pytest.raises(ResearchNoAConfigError):
        replace(operator_p5_nominal_config(tmp_path), **invalid_update)


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"activation_speed_hold": False},
        {"speed_policy": ResearchSpeedPolicy(35.0, True)},
        {"p6": P6Config(1, "synthetic")},
        {"development_validation": True},
        {
            "exit_assistance": ResearchExitAssistanceConfig(
                ROUTE_MANIFEST,
                ROUTE_ID,
                "training:wrong-context",
                context=ExitContext.MODULE,
            )
        },
    ],
)
def test_operator_p5_training_rejects_partial_or_nontraining_context(
    tmp_path: Path,
    invalid_update,
) -> None:
    with pytest.raises(ResearchNoAConfigError):
        replace(operator_p5_training_config(tmp_path), **invalid_update)


def test_generic_noa_only_p5_without_operator_protocol_flag_is_rejected(
    tmp_path: Path,
) -> None:
    config = operator_p5_nominal_config(tmp_path)

    with pytest.raises(ResearchNoAConfigError, match="speed_policy"):
        replace(config, duration=60.0, operator_nominal_module=False)


def test_legacy_high_speed_p5_remains_rejected(tmp_path: Path) -> None:
    base = make_config(tmp_path, "legacy-high-speed-p5")

    with pytest.raises(ResearchNoAConfigError, match="speed_policy"):
        replace(
            base,
            speed_policy=ResearchSpeedPolicy(35.0, True),
            exit_assistance=ResearchExitAssistanceConfig(
                ROUTE_MANIFEST,
                ROUTE_ID,
                "legacy:high-speed:exit",
            ),
        )


def test_legacy_low_speed_p5_remains_allowed(tmp_path: Path) -> None:
    base = make_config(tmp_path, "legacy-p5")

    config = replace(
        base,
        exit_assistance=ResearchExitAssistanceConfig(
            ROUTE_MANIFEST,
            ROUTE_ID,
            "legacy:low-speed:exit",
        ),
    )

    assert config.exit_assistance is not None and config.speed_policy is None


def test_operator_p5_uses_existing_runtime_coordinator_and_view_binding(
    tmp_path: Path,
) -> None:
    config = operator_p5_nominal_config(tmp_path)
    world = FakeResearchWorld(FakeResearchVehicle())
    world.map.name = "Carla/Maps/Town04"
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        exit_route_validator=lambda route, carla_map, hero: None,
    )

    with runner.session() as session:
        assert session.exit_assistance is not None
        assert session.exit_view_binding is not None
        assert viewer_factory.created[0].exit_view_binding is session.exit_view_binding


def test_operator_p5_training_f8_completion_reports_normal_exit(
    tmp_path: Path,
) -> None:
    world = FakeResearchWorld(FakeResearchVehicle())
    world.map.name = "Carla/Maps/Town04"
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        operator_p5_training_config(tmp_path),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        exit_route_validator=lambda route, carla_map, hero: None,
    )

    with runner.session() as session:
        viewer_factory.created[0].training_completed_early = True
        report = session.run()

        assert report is not None and report.training_completed_early is True
        assert session.exit_assistance is not None
        payload = json.loads(session.exit_assistance.events[-1].payload_json)
        assert payload["termination_reason"] == "NORMAL"
