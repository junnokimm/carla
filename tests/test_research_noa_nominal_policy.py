from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.context import ExperimentModule, ExperimentPhase
from src.p6.config import P6Config
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_config import ResearchNoAConfigError
from src.scenario.research_noa_speed_policy import (
    ResearchSpeedPolicy,
    ResearchSpeedPolicyError,
    resolve_speed_policy,
)
from tests.test_p4_research_noa_integration import (
    make_config,
    make_module_two_config,
)


def nominal_config(
    output_dir: Path,
    module: ExperimentModule = ExperimentModule.MODULE_1,
):
    base = (
        make_config(output_dir, "nominal-module")
        if module is ExperimentModule.MODULE_1
        else make_module_two_config(output_dir, "nominal-module")
    )
    assert base.persistence is not None
    assert base.automation_interaction is not None
    duration, stage = (
        (600.0, "MODULE_1_RUNNING")
        if module is ExperimentModule.MODULE_1
        else (780.0, "MODULE_2_RUNNING")
    )
    longitudinal = replace(
        base.control_config.longitudinal,
        target_speed_kmh=None,
        max_throttle=1.0,
    )
    return replace(
        base,
        duration=duration,
        control_config=replace(base.control_config, longitudinal=longitudinal),
        persistence=replace(
            base.persistence,
            stage_label=stage,
            segment_phase=None,
        ),
        automation_interaction=replace(
            base.automation_interaction,
            initial_stage=stage,
        ),
        speed_policy=ResearchSpeedPolicy(110.0, noa_only=True),
        operator_nominal_module=True,
        activation_speed_hold=True,
    )


@pytest.mark.parametrize(
    "mode",
    [DrivingControlMode.MANUAL, DrivingControlMode.NOA_ACTIVE],
)
def test_noa_only_policy_resolves_mode_specific_guard(mode: DrivingControlMode) -> None:
    policy = ResearchSpeedPolicy(110.0, noa_only=True)
    config = nominal_config(Path("unused"))

    effective = replace(config, speed_policy=policy).effective_speed_policy

    assert effective.noa_only is True
    assert effective.guard_for_mode(mode) == (
        None if mode is DrivingControlMode.MANUAL else 110.0
    )
    assert effective.manifest(config.control_config)["noa_only"] is True


@pytest.mark.parametrize(
    ("guard", "high_ack"),
    [
        (109.0, False),
        (110.0, True),
    ],
)
def test_noa_only_policy_rejects_nonexclusive_combinations(
    guard: float,
    high_ack: bool,
) -> None:
    with pytest.raises(ResearchSpeedPolicyError, match="noa_only"):
        ResearchSpeedPolicy(guard, high_ack, noa_only=True)


@pytest.mark.parametrize(
    ("module", "duration"),
    [
        (ExperimentModule.MODULE_1, 600.0),
        (ExperimentModule.MODULE_2, 780.0),
    ],
)
def test_nominal_module_accepts_only_assigned_exact_protocol_context(
    tmp_path: Path,
    module: ExperimentModule,
    duration: float,
) -> None:
    config = nominal_config(tmp_path, module)

    assert config.duration == duration
    assert config.operator_nominal_module is True
    assert config.effective_speed_policy.noa_only is True
    assert config.effective_speed_policy.target_pending is True
    assert config.control_config.longitudinal.max_throttle == 1.0


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"duration": 599.0},
        {"operator_training": True},
        {"speed_policy": ResearchSpeedPolicy(35.0, True)},
        {"p6": P6Config(1, "synthetic")},
        {
            "exit_assistance": ResearchExitAssistanceConfig(
                Path("route.json"), "route", "event"
            )
        },
        {"development_validation": True},
    ],
)
def test_nominal_module_rejects_nonprotocol_context(
    tmp_path: Path,
    invalid_update,
) -> None:
    with pytest.raises(ResearchNoAConfigError, match="operator_nominal_module"):
        replace(nominal_config(tmp_path), **invalid_update)


def test_nominal_module_rejects_training_segment_and_wrong_stage(
    tmp_path: Path,
) -> None:
    config = nominal_config(tmp_path)
    assert config.persistence is not None
    assert config.automation_interaction is not None

    with pytest.raises(ResearchNoAConfigError, match="operator_nominal_module"):
        replace(
            config,
            persistence=replace(
                config.persistence,
                segment_phase=ExperimentPhase.TRAINING,
            ),
        )
    with pytest.raises(ResearchNoAConfigError, match="operator_nominal_module"):
        replace(
            config,
            automation_interaction=replace(
                config.automation_interaction,
                initial_stage="MODULE_2_RUNNING",
            ),
        )


def test_activation_hold_policy_resolves_pending_target_honestly() -> None:
    effective = resolve_speed_policy(
        None,
        ResearchSpeedPolicy(110.0, noa_only=True),
        activation_speed_hold=True,
    )

    assert effective.desired_speed_kmh is None
    assert effective.effective_desired_speed_kmh is None
    assert effective.target_source == "ACTIVATION_SNAPSHOT"
    assert effective.target_pending is True


def test_pending_target_is_rejected_without_activation_hold() -> None:
    with pytest.raises(ResearchSpeedPolicyError, match="target_speed_kmh"):
        resolve_speed_policy(None, ResearchSpeedPolicy(110.0, noa_only=True))
