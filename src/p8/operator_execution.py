from __future__ import annotations

import csv
from dataclasses import dataclass, replace

from src.experiment.context import ExperimentModule, ExperimentPhase
from src.p8.model import P8Error, Stage
from src.p8.operator_exit_ledger_view import LiveCoordinatorEvidence
from src.p8.operator_exit_result import (
    ExitOpportunityResult,
    configured_exit_result,
    not_configured_exit_result,
    technical_exit_result,
)
from src.p8.operator_live_evidence import after_cleanup_evidence, live_evidence
from src.p8.operator_metrics import event_count, reduce_module_run
from src.p8.operator_model import ModuleRunResult, OperatorRuntimeConfig
from src.p8.services import SessionServices
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
class OperatorRunExecutor:
    services: SessionServices
    runtime: OperatorRuntimeConfig
    runs: list[ModuleRunResult]
    exit_slots: dict[Stage, ExitOpportunityResult]

    def execute(self, request: OperatorRunRequest) -> ModuleRunResult:
        self.runtime.validate_safety_contract()
        if any(result.stage is request.stage for result in self.runs):
            raise P8Error("ONE_ACTUAL_BOUNDED_RUN_PER_STAGE")
        exit_template = (
            self.runtime.training_exit
            if request.stage is Stage.TRAINING
            else self.runtime.module_exit
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
                    f"{self.services.context.study_run_id}:"
                    f"{request.stage.value}:{exit_template.route_id}"
                ),
                eligibility_open_s=(
                    exit_template.eligibility_open_s
                    if request.stage is Stage.TRAINING
                    else self.runtime.module_1_exit_window.open_s
                    if request.module is ExperimentModule.MODULE_1
                    and self.runtime.module_1_exit_window is not None
                    else self.runtime.module_2_exit_window.open_s
                    if request.module is ExperimentModule.MODULE_2
                    and self.runtime.module_2_exit_window is not None
                    else exit_template.eligibility_open_s
                ),
                eligibility_close_s=(
                    exit_template.eligibility_close_s
                    if request.stage is Stage.TRAINING
                    else self.runtime.module_1_exit_window.close_s
                    if request.module is ExperimentModule.MODULE_1
                    and self.runtime.module_1_exit_window is not None
                    else self.runtime.module_2_exit_window.close_s
                    if request.module is ExperimentModule.MODULE_2
                    and self.runtime.module_2_exit_window is not None
                    else exit_template.eligibility_close_s
                ),
            )
        )
        path = self.services.logger.event_path
        runtime_started_session_s = self.services.elapsed
        live: LiveCoordinatorEvidence | None = None
        actual = None
        primary_error: BaseException | None = None
        secondary_errors: list[BaseException] = []
        try:
            condition = (
                self.services.context.module_1_condition
                if request.module is ExperimentModule.MODULE_1
                else self.services.context.module_2_condition
            )
            persistence = ResearchPersistenceConfig(
                self.services.assignment,
                self.services.context.study_run_id,
                self.services.directory,
                request.module,
                request.stage.value
                if request.nominal_request
                else f"P8_OPERATOR_{request.stage.value}_BOUNDED_PARTIAL",
                segment_phase=request.segment_phase,
            )
            interaction = ResearchAutomationInteractionConfig(
                request.module,
                condition,
                self.runtime.interaction_policy,
                initial_stage=request.stage.value,
            )
            config = ResearchNoARunConfig(
                duration=request.duration,
                control_config=self.runtime.control_config,
                speed_policy=self.runtime.speed_policy,
                mode=ResearchNoARunMode.LIVE_SMOKE,
                spawn_index=self.runtime.spawn_index,
                host=self.runtime.host,
                port=self.runtime.port,
                timeout=self.runtime.timeout,
                run_id=str(self.services.context.study_run_id),
                automation_interaction=interaction,
                persistence=persistence,
                rear_mirror_enabled=self.runtime.rear_mirror_enabled,
                operator_training=request.stage is Stage.TRAINING,
                operator_nominal_module=request.nominal_request,
                activation_speed_hold=False,
                exit_assistance=exit_config,
                traffic_manager_port=self.runtime.traffic_manager_port,
            )
            offset = event_count(path)
            runner = self.runtime.runner_builder(
                config,
                self.services.borrowed_research_log(),
            )
            try:
                with runner.session() as actual:
                    try:
                        if (
                            actual.persistence is None
                            or actual.persistence.logger is not self.services.logger
                            or actual.persistence.study_run is not self.services.context
                        ):
                            raise P8Error("OPERATOR_SHARED_RESOURCE_IDENTITY_MISMATCH")
                        report = actual.run()
                    except (
                        KeyboardInterrupt,
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                    ) as error:
                        primary_error = error
                    try:
                        live = live_evidence(actual.exit_assistance)
                    except (OSError, TypeError, ValueError, KeyError) as error:
                        secondary_errors.append(error)
            except (
                KeyboardInterrupt,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                if primary_error is None:
                    primary_error = error
                else:
                    secondary_errors.append(error)
            if actual is not None:
                try:
                    live = after_cleanup_evidence(live, actual.exit_assistance)
                except (OSError, TypeError, ValueError, KeyError) as error:
                    secondary_errors.append(error)
            if primary_error is None and secondary_errors:
                primary_error = secondary_errors.pop(0)
            if primary_error is not None:
                for secondary_error in secondary_errors:
                    primary_error.add_note(
                        "Secondary evidence/cleanup failure: "
                        f"{type(secondary_error).__name__}:{secondary_error}"
                    )
                raise primary_error
            if report is None:
                raise P8Error("ACTUAL_BOUNDED_RUN_REPORT_MISSING")
            result = reduce_module_run(
                path,
                offset=offset,
                stage=request.stage,
                requested_duration_s=request.duration,
                actual_active_drive_s=report.active_drive_seconds,
                user_exited=report.user_exited,
                planned_duration_s=(
                    600.0
                    if request.stage in (Stage.TRAINING, Stage.MODULE_1_RUNNING)
                    else 780.0
                ),
                runtime_started_session_s=runtime_started_session_s,
                runtime_closed_session_s=self.services.elapsed,
                completion_reason=(
                    "OPERATOR_EARLY_FINISH"
                    if report.training_completed_early
                    else "USER_ABORT"
                    if report.user_exited
                    else "TIME_LIMIT_REACHED"
                ),
                training_completed_early=report.training_completed_early,
                nominal_request=request.nominal_request,
            )
            exit_result = (
                not_configured_exit_result()
                if exit_config is None
                else configured_exit_result(
                    path,
                    exit_config,
                    live=live,
                    user_exited=report.user_exited,
                    participant_id=str(self.services.context.participant_id),
                )
            )
        except (
            KeyboardInterrupt,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            if exit_config is None:
                exit_result = not_configured_exit_result()
            else:
                try:
                    exit_result = configured_exit_result(
                        path,
                        exit_config,
                        technical_error=error,
                        live=live,
                        participant_id=str(self.services.context.participant_id),
                        secondary_errors=tuple(secondary_errors),
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                    csv.Error,
                ) as reduction_error:
                    error.add_note(
                        "Secondary evidence reduction failure: "
                        f"{type(reduction_error).__name__}:{reduction_error}"
                    )
                    exit_result = technical_exit_result(
                        exit_config,
                        error,
                        live,
                        (*secondary_errors, reduction_error),
                    )
            self.exit_slots[request.stage] = exit_result
            raise
        result = replace(result, exit_opportunity=exit_result)
        self.exit_slots[request.stage] = exit_result
        self.runs.append(result)
        self.services.emit("p8_actual_bounded_run", result.manifest())
        return result
