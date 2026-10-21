from __future__ import annotations

from dataclasses import dataclass, replace

from src.experiment.context import ExperimentModule, ExperimentPhase
from src.p8.model import P8Error, Stage
from src.p8.operator_model import OperatorRuntimeConfig
from src.p8.services import SessionServices
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig


@dataclass(frozen=True, slots=True)
class OperatorRunRequest:
    module: ExperimentModule
    stage: Stage
    duration: float
    segment_phase: ExperimentPhase | None
    nominal_request: bool = False


@dataclass(frozen=True, slots=True)
class PreparedOperatorRun:
    config: ResearchNoARunConfig
    exit_config: ResearchExitAssistanceConfig | None


def prepare_operator_run(
    services: SessionServices,
    runtime: OperatorRuntimeConfig,
    request: OperatorRunRequest,
) -> PreparedOperatorRun:
    runtime.validate_safety_contract()
    exit_template = (
        runtime.training_exit
        if request.stage is Stage.TRAINING
        else runtime.module_exit
    )
    if (
        exit_template is not None
        and request.stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING)
        and not request.nominal_request
    ):
        raise P8Error("P5_MODULE_EXIT_REQUIRES_NOMINAL_WINDOW")
    limit = 600.0 if request.stage is Stage.TRAINING else 60.0
    if request.nominal_request:
        limit = 600.0 if request.stage is Stage.MODULE_1_RUNNING else 780.0
    if not 1.0 <= request.duration <= limit:
        raise P8Error(f"ACTUAL_RUN_DURATION_REQUIRES_1_TO_{round(limit)}_SECONDS")
    exit_config = (
        None
        if exit_template is None
        else replace(
            exit_template,
            lc_event_id=(
                f"{services.context.study_run_id}:"
                f"{request.stage.value}:{exit_template.route_id}"
            ),
            eligibility_open_s=(
                exit_template.eligibility_open_s
                if request.stage is Stage.TRAINING
                else runtime.module_1_exit_window.open_s
                if request.module is ExperimentModule.MODULE_1
                and runtime.module_1_exit_window is not None
                else runtime.module_2_exit_window.open_s
                if request.module is ExperimentModule.MODULE_2
                and runtime.module_2_exit_window is not None
                else exit_template.eligibility_open_s
            ),
            eligibility_close_s=(
                exit_template.eligibility_close_s
                if request.stage is Stage.TRAINING
                else runtime.module_1_exit_window.close_s
                if request.module is ExperimentModule.MODULE_1
                and runtime.module_1_exit_window is not None
                else runtime.module_2_exit_window.close_s
                if request.module is ExperimentModule.MODULE_2
                and runtime.module_2_exit_window is not None
                else exit_template.eligibility_close_s
            ),
        )
    )
    condition = (
        services.context.module_1_condition
        if request.module is ExperimentModule.MODULE_1
        else services.context.module_2_condition
    )
    persistence = ResearchPersistenceConfig(
        services.assignment,
        services.context.study_run_id,
        services.directory,
        request.module,
        request.stage.value
        if request.nominal_request
        else f"P8_OPERATOR_{request.stage.value}_BOUNDED_PARTIAL",
        segment_phase=request.segment_phase,
    )
    interaction = ResearchAutomationInteractionConfig(
        request.module,
        condition,
        runtime.interaction_policy,
        initial_stage=request.stage.value,
    )
    return PreparedOperatorRun(
        ResearchNoARunConfig(
            duration=request.duration,
            control_config=runtime.control_config,
            speed_policy=runtime.speed_policy,
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=runtime.spawn_index,
            host=runtime.host,
            port=runtime.port,
            timeout=runtime.timeout,
            run_id=str(services.context.study_run_id),
            automation_interaction=interaction,
            persistence=persistence,
            rear_mirror_enabled=runtime.rear_mirror_enabled,
            operator_training=request.stage is Stage.TRAINING,
            operator_nominal_module=request.nominal_request,
            operator_clear_noon=True,
            activation_speed_hold=False,
            exit_assistance=exit_config,
            traffic_manager_port=runtime.traffic_manager_port,
            camera_diagnostics=True,
            native_tm_diagnostics=runtime.native_tm_diagnostics,
            tsxw_config=runtime.tsxw_config,
            vehicle_audio=True,
        ),
        exit_config,
    )
