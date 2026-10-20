from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from src.error_notes import append_error_note
from src.p7.occurrence import OccurrenceRegistry
from src.p8.hazard_collision_stop import COLLISION_END
from src.p8.model import Mode, P8Error, Stage
from src.p8.operator_continuity import OperatorRuntimeContinuity
from src.p8.operator_exit_ledger_view import LiveCoordinatorEvidence
from src.p8.operator_exit_result import (
    ExitOpportunityResult,
    configured_exit_result,
    not_configured_exit_result,
    technical_exit_result,
)
from src.p8.operator_live_evidence import after_cleanup_evidence, live_evidence
from src.p8.clear_road_hazard import ClearRoadM2Hazard
from src.p8.operator_metrics import event_count, reduce_module_run
from src.p8.operator_model import ModuleRunResult, OperatorRuntimeConfig
from src.p8.operator_recovery import OperatorRecovery
from src.p8.operator_run_config import OperatorRunRequest, prepare_operator_run
from src.p8.pilot_gaze import collect_pilot_gaze
from src.p8.services import SessionServices
from src.scenario.driver_view import RuntimeFrameContinuation
from src.scenario.research_noa_runtime import ResearchNoARunner


def require_offline_hazard_runner(mode: Mode, runner: ResearchNoARunner) -> None:
    if mode is not Mode.DEV_REHEARSAL:
        raise P8Error("HAZARD_EXECUTION_REQUIRES_DEV_REHEARSAL")
    if runner.client_source != "INJECTED_TEST_CLIENT":
        raise P8Error("HAZARD_EXECUTION_REQUIRES_INJECTED_TEST_CLIENT")
    if runner.viewer_source != "INJECTED_TEST_VIEWER":
        raise P8Error("HAZARD_EXECUTION_REQUIRES_INJECTED_TEST_VIEWER")
    if runner.driver_input_source != "INJECTED_TEST_DRIVER_INPUT":
        raise P8Error("HAZARD_EXECUTION_REQUIRES_INJECTED_TEST_DRIVER_INPUT")


@dataclass(frozen=True, slots=True)
class OperatorRunExecutor:
    services: SessionServices
    runtime: OperatorRuntimeConfig
    runs: list[ModuleRunResult]
    exit_slots: dict[Stage, ExitOpportunityResult]
    continuity: OperatorRuntimeContinuity
    nominal_m2_handoff: Callable[[], RuntimeFrameContinuation | None]
    occurrences: OccurrenceRegistry
    hazard_record_sink: Callable
    runtime_faults: list = field(default_factory=list)

    def execute(self, request: OperatorRunRequest) -> ModuleRunResult:
        if any(result.stage is request.stage for result in self.runs):
            raise P8Error("ONE_ACTUAL_BOUNDED_RUN_PER_STAGE")
        prepared = prepare_operator_run(self.services, self.runtime, request)
        exit_config = prepared.exit_config
        path = self.services.logger.event_path
        runtime_started_session_s = self.services.elapsed
        live: LiveCoordinatorEvidence | None = None
        actual = None
        report = None
        runner: ResearchNoARunner | None = None
        primary_error: BaseException | None = None
        secondary_errors: list[BaseException] = []
        try:
            offset = (self.services.logger.event_count if hasattr(self.services.logger, "event_count")
                      else event_count(path))
            runner = self.runtime.runner_builder(
                prepared.config,
                self.services.borrowed_research_log(),
            )
            m2_nominal = (
                request.stage is Stage.MODULE_2_RUNNING and request.nominal_request
            )
            v1_enabled = m2_nominal and self.runtime.m2_hazard_v1 is not None
            if v1_enabled and self.services.mode is not Mode.DEV_REHEARSAL:
                raise P8Error("M2_V1_REQUIRES_DEV_REHEARSAL")
            handoff_enabled = m2_nominal and not v1_enabled
            if handoff_enabled and self.runtime.hazard_execution is not None:
                require_offline_hazard_runner(self.services.mode, runner)
            try:
                with self.continuity.session(
                    runner,
                    self.services,
                    handoff_enabled=handoff_enabled,
                ) as actual:
                    try:
                        actual.viewer.operator_recovery = OperatorRecovery(
                            actual, self.services, self.runtime_faults
                        )
                        if (
                            actual.persistence is None
                            or actual.persistence.logger is not self.services.logger
                            or actual.persistence.study_run is not self.services.context
                        ):
                            raise P8Error("OPERATOR_SHARED_RESOURCE_IDENTITY_MISMATCH")
                        if v1_enabled:
                            actual.active_scene_factory = lambda session, origin: (
                                ClearRoadM2Hazard(
                                    session,
                                    self.services,
                                    self.runtime.m2_hazard_v1,
                                    self.occurrences,
                                    self.hazard_record_sink,
                                    origin,
                                    {
                                        "client": runner.client_source,
                                        "viewer": runner.viewer_source,
                                        "driver_input": runner.driver_input_source,
                                    },
                                )
                            )
                        with collect_pilot_gaze(
                            actual.viewer, self.services, request.stage, self.runtime.gaze_config
                        ):
                            report = actual.run(
                                continuation_factory=(
                                    (lambda _: self.nominal_m2_handoff())
                                    if handoff_enabled
                                    else None
                                )
                            )
                    except KeyboardInterrupt as error:
                        primary_error = error
                        if actual.native_tm_collector is not None:
                            actual.native_tm_collector.mark_interrupted(error)
                    except (
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
                elif error is not primary_error:
                    secondary_errors.append(error)
            if actual is not None:
                try:
                    live = after_cleanup_evidence(live, actual.exit_assistance)
                except (OSError, TypeError, ValueError, KeyError) as error:
                    secondary_errors.append(error)
            diagnostic_status = (
                actual.native_tm_collector.status
                if actual is not None and actual.native_tm_collector is not None
                else None
                if runner is None
                else runner.native_tm_diagnostics_status
            )
            if diagnostic_status is not None:
                try:
                    self.services.emit(
                        "p8_native_tm_diagnostics_status",
                        diagnostic_status.payload(),
                        stage=request.stage,
                    )
                except (
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    KeyError,
                ) as error:
                    secondary_errors.append(error)
            if primary_error is None and secondary_errors:
                primary_error = secondary_errors.pop(0)
            if primary_error is not None:
                for secondary_error in secondary_errors:
                    append_error_note(
                        primary_error,
                        "Secondary evidence/cleanup failure: "
                        f"{type(secondary_error).__name__}:{secondary_error}",
                    )
                raise primary_error
            if report is None:
                raise P8Error("ACTUAL_BOUNDED_RUN_REPORT_MISSING")
            collision_stop = getattr(actual.viewer, "collision_stop", None)
            result = reduce_module_run(
                path,
                fallback_rows=(self.services.logger.fallback_rows(offset)
                               if getattr(self.services.logger, "collection_failure_count", 0) else None),
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
                    COLLISION_END if collision_stop is not None else
                    "OPERATOR_EARLY_FINISH"
                    if report.training_completed_early
                    else "USER_ABORT"
                    if report.user_exited
                    else "COMPLETED_WITH_RUNTIME_INTERRUPTION"
                    if actual is not None and actual.viewer.operator_recovery.paused_seconds > 0
                    else "TIME_LIMIT_REACHED"
                ),
                training_completed_early=report.training_completed_early,
                nominal_request=request.nominal_request,
            )
            if collision_stop is not None:
                result = replace(result, collision_stop=dict(collision_stop.record))
            exit_result = (
                not_configured_exit_result()
                if exit_config is None
                else technical_exit_result(exit_config, RuntimeError("LOG_STORAGE_INCOMPLETE"), live, ())
                if getattr(self.services.logger, "collection_failure_count", 0)
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
                    append_error_note(
                        error,
                        "Secondary evidence reduction failure: "
                        f"{type(reduction_error).__name__}:{reduction_error}",
                    )
                    exit_result = technical_exit_result(
                        exit_config,
                        error,
                        live,
                        (*secondary_errors, reduction_error),
                    )
            self.exit_slots[request.stage] = exit_result
            collision_stop = None if actual is None else getattr(actual.viewer, "collision_stop", None)
            if collision_stop is not None:
                collision_stop.record["errors"].append({
                    "step": "run_finalization", "error": f"{type(error).__name__}:{error}"
                })
                try:
                    partial = reduce_module_run(
                        path, offset=offset, stage=request.stage,
                        requested_duration_s=request.duration,
                        actual_active_drive_s=collision_stop.active_seconds,
                        user_exited=False, planned_duration_s=780.0,
                        runtime_started_session_s=runtime_started_session_s,
                        runtime_closed_session_s=self.services.elapsed,
                        completion_reason=COLLISION_END,
                        training_completed_early=False, nominal_request=request.nominal_request,
                    )
                    partial = replace(partial, exit_opportunity=exit_result,
                                      collision_stop=dict(collision_stop.record))
                    self.runs.append(partial)
                    self.services.emit("p8_actual_bounded_run", partial.manifest(), stage=request.stage)
                except Exception as reduction_error:  # noqa: BLE001 -- preserve the original failure
                    append_error_note(error, f"Collision partial reduction failed: {reduction_error}")
            raise
        result = replace(result, exit_opportunity=exit_result)
        self.exit_slots[request.stage] = exit_result
        self.runs.append(result)
        self.services.emit(
            "p8_actual_bounded_run",
            result.manifest(),
            stage=request.stage,
        )
        return result
