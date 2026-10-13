from __future__ import annotations

from collections.abc import Callable
from typing import assert_never

from src.experiment.context import ExperimentModule, ExperimentPhase
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import Json
from src.p8.model import P8Error, Stage
from src.p8.operator_continuity import OperatorRuntimeContinuity
from src.p8.operator_execution import OperatorRunExecutor
from src.p8.operator_exit_result import (
    ExitOpportunityClassification,
    ExitOpportunityResult,
)
from src.p8.operator_hazard import (
    OperatorHazardLoop,
)
from src.p8.operator_hazard_contracts import HazardExecutionResources
from src.p8.operator_hazard_record import blocked_hazard_record
from src.p8.operator_model import ModuleRunResult, OperatorRuntimeConfig, TrainingRun
from src.p8.operator_run_config import OperatorRunRequest
from src.p8.services import SessionServices
from src.scenario.driver_view import RuntimeFrameContinuation


class OperatorComponents:
    kind = "ACTUAL_BOUNDED_OPERATOR"

    def __init__(
        self,
        services: SessionServices,
        occurrences: OccurrenceRegistry,
        runtime: OperatorRuntimeConfig,
        nominal_m2_handoff: Callable[[], RuntimeFrameContinuation | None],
    ) -> None:
        self.services = services
        self.occurrences = occurrences
        self.runtime = runtime
        self.nominal_m2_handoff = nominal_m2_handoff
        self.runs: list[ModuleRunResult] = []
        self.exit_slots: dict[Stage, ExitOpportunityResult] = {}
        self.post: dict[str, Json] = {
            "survey": "NOT_RECORDED",
            "debrief": "NOT_RECORDED",
            "data_use": "NOT_RECORDED",
            "analysis_eligible": False,
            "deletion_status": "NOT_REQUESTED",
        }
        self.clean = False
        self.hazard_observation: dict[str, Json] | None = None
        self.hazard_loop: OperatorHazardLoop | None = None
        self.continuity = OperatorRuntimeContinuity()

    def enter(self, stage: Stage) -> None:
        if stage is not self.services.stage:
            raise P8Error("COMPONENT_STAGE_CONTEXT_MISMATCH")
        if stage is Stage.FINAL_HAZARD and self.hazard_observation is None:
            timestamp = self.services.stamp()
            identities = (
                self.continuity.bind_final_hazard(self.services)
                if self.continuity.active
                else None
            )
            resources = (
                None
                if identities is None
                else HazardExecutionResources(
                    self.continuity.bound_session,
                    self.services,
                    identities,
                )
            )
            plan = self.runtime.hazard_execution
            if plan is None or resources is None:
                record = blocked_hazard_record(resources, self.services, timestamp)
                self.services.emit("hazard_observation", record, timestamp=timestamp)
                self.hazard_observation = record
            else:
                self.hazard_loop = OperatorHazardLoop(
                    plan,
                    resources,
                    self.occurrences,
                    self._store_hazard_record,
                )

    def _store_hazard_record(self, record: dict[str, Json]) -> None:
        self.hazard_observation = record

    def leave(self, stage: Stage) -> None:
        return

    def tick(self) -> None:
        return

    def completion_blocker(self, stage: Stage) -> str | None:
        if stage is Stage.TRAINING:
            run = next((result for result in self.runs if result.stage is stage), None)
            if run is None or run.actual_active_drive_s <= 0.0:
                return "POSITIVE_ACTUAL_TRAINING_RUN_REQUIRED"
        if stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING):
            run = next((result for result in self.runs if result.stage is stage), None)
            if run is None:
                return "ACTUAL_BOUNDED_MODULE_RUN_REQUIRED"
            if run.window_complete:
                exit_result = run.exit_opportunity
                if exit_result is None:
                    return "P5_EXIT_EVIDENCE_MISSING"
                match exit_result.classification:
                    case ExitOpportunityClassification.NOT_CONFIGURED:
                        return "P5_EXIT_NOT_CONFIGURED"
                    case ExitOpportunityClassification.CONFIGURED_NOT_RUN:
                        return "P5_EXIT_CONFIGURED_NOT_RUN"
                    case ExitOpportunityClassification.CONFIGURED_NOT_PRESENTED:
                        return "P5_EXIT_CONFIGURED_NOT_PRESENTED"
                    case ExitOpportunityClassification.INCOMPLETE:
                        return "P5_EXIT_EVIDENCE_INCOMPLETE"
                    case ExitOpportunityClassification.TECHNICAL_ERROR:
                        return "P5_EXIT_TECHNICAL_ERROR"
                    case ExitOpportunityClassification.BLOCKED:
                        return "P5_ASSISTED_EXECUTION_BLOCKED_UNVERIFIED"
                    case (
                        ExitOpportunityClassification.PRESENTED
                        | ExitOpportunityClassification.DECISION_RECORDED
                        | ExitOpportunityClassification.PARTICIPANT_INTERRUPTED
                    ):
                        return "P5_EXIT_EVIDENCE_INCOMPLETE"
                    case (
                        ExitOpportunityClassification.NORMAL_MISSED
                        | ExitOpportunityClassification.EXIT_CONFIRMED
                    ):
                        return "P6_EYE_TRACKING_INTEGRATION_BLOCKED"
                    case unreachable:
                        assert_never(unreachable)
            return "FULL_ROUTE_EXIT_AND_NOMINAL_WINDOW_BLOCKED"
        if stage is Stage.FINAL_HAZARD:
            if (
                self.hazard_observation is not None
                and self.hazard_observation.get("execution_status")
                == "EXECUTED_OFFLINE"
            ):
                return "PHYSICAL_HAZARD_VALIDATION_BLOCKED"
            return "MOVING_HAZARD_INTEGRATION_BLOCKED"
        return None

    def record_receipt(self, name: str, status: str, value: Json) -> None:
        if self.services.stage is not Stage.POST_EXPERIMENT:
            return
        self.post[name] = {"status": status, "value": value}
        if name == "data_use":
            refused = status == "REFUSED" or value is False
            self.post["analysis_eligible"] = False
            self.post["deletion_status"] = (
                "PENDING_APPROVED_PROCEDURE" if refused else "NOT_REQUESTED"
            )

    def execute_training(
        self, training: TrainingRun, duration: float
    ) -> ModuleRunResult:
        if self.services.stage is not Stage.TRAINING:
            raise P8Error("TRAINING_RUN_OUTSIDE_TRAINING_STAGE")
        match training:
            case TrainingRun.MANUAL_NOA:
                return self._execute(
                    ExperimentModule.MODULE_1,
                    Stage.TRAINING,
                    duration,
                    ExperimentPhase.TRAINING,
                )
            case unreachable:
                assert_never(unreachable)

    def execute_current_module(self, duration: float | None = None) -> ModuleRunResult:
        match self.services.stage:
            case Stage.MODULE_1_RUNNING:
                module = ExperimentModule.MODULE_1
            case Stage.MODULE_2_RUNNING:
                module = ExperimentModule.MODULE_2
            case (
                Stage.SESSION_CREATED
                | Stage.CONSENT_AND_ELIGIBILITY
                | Stage.TRAINING
                | Stage.EYE_TRACKER_CALIBRATION
                | Stage.MODULE_1_PREPARE
                | Stage.MODULE_1_COMPLETE
                | Stage.REST
                | Stage.MODULE_2_PREPARE
                | Stage.FINAL_HAZARD
                | Stage.POST_EXPERIMENT
                | Stage.SESSION_COMPLETE
                | Stage.ABORTED
            ):
                raise P8Error("ACTUAL_RUN_REQUIRES_RUNNING_MODULE_STAGE")
            case unreachable:
                assert_never(unreachable)
        planned_duration = 600.0 if module is ExperimentModule.MODULE_1 else 780.0
        nominal_request = duration is None
        selected_duration = planned_duration if duration is None else duration
        return self._execute(
            module,
            self.services.stage,
            selected_duration,
            None,
            nominal_request=nominal_request,
        )

    def _execute(
        self,
        module: ExperimentModule,
        stage: Stage,
        duration: float,
        segment_phase: ExperimentPhase | None,
        *,
        nominal_request: bool = False,
    ) -> ModuleRunResult:
        return OperatorRunExecutor(
            self.services,
            self.runtime,
            self.runs,
            self.exit_slots,
            self.continuity,
            self.nominal_m2_handoff,
        ).execute(
            OperatorRunRequest(
                module,
                stage,
                duration,
                segment_phase,
                nominal_request,
            )
        )

    def flush(self) -> None:
        return

    def cleanup(self) -> None:
        self.clean = True

    def snapshot(self) -> dict[str, Json]:
        return {
            "provenance": self.kind,
            "module_runs": [result.manifest() for result in self.runs],
            "exit_slots": {
                stage.value: result.manifest()
                for stage, result in self.exit_slots.items()
            },
            "hazard_state": (
                "BLOCKED_NOT_RUN"
                if self.hazard_observation is None
                else str(self.hazard_observation.get("hazard_state", "BLOCKED_NOT_RUN"))
            ),
            "hazard_observation": (
                None
                if self.hazard_observation is None
                else dict(self.hazard_observation)
            ),
            "post": dict(self.post),
            "owned_resource_tokens": list(self.continuity.tokens()),
            "cleanup_completed": self.clean,
        }
