from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from src.p7.occurrence import OccurrenceRegistry
from src.p8.model import RECEIPTS, SEQUENCE, Mode, P8Error, Stage
from src.p8.operator_components import OperatorComponents
from src.p8.operator_model import (
    DevOutcome,
    DoneClaim,
    ModuleRunResult,
    OperatorRuntimeConfig,
    OperatorSnapshot,
    Receipt,
    RegistrationRequest,
    TrainingRun,
)
from src.p8.operator_registration import claim_registration
from src.p8.orchestrator import ExperimentSession
from src.p8.readiness import Readiness, require_participant_ready
from src.p8.services import SessionServices
from src.p8.tsxw_input import TsxwDeviceError
from src.p8.tsxw_preparation import CANCELLED
from src.scenario.driver_view import RuntimeFrameContinuation


class OperatorController:
    def __init__(
        self,
        root: Path,
        *,
        mode: Mode,
        runtime: OperatorRuntimeConfig,
        readiness: Readiness | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if mode not in (Mode.DEV_REHEARSAL, Mode.PARTICIPANT):
            raise P8Error("OPERATOR_MODE_MUST_BE_DEV_REHEARSAL_OR_PARTICIPANT")
        self.root = root
        self.mode = mode
        self.runtime = runtime
        self.readiness = readiness or Readiness()
        self._monotonic_ns = monotonic_ns
        self._utc_ns = utc_ns
        self._session: ExperimentSession | None = None
        self._components: OperatorComponents | None = None

    @property
    def session(self) -> ExperimentSession:
        if self._session is None:
            raise P8Error("OPERATOR_SESSION_NOT_REGISTERED")
        return self._session

    def register_id(self, request: RegistrationRequest) -> OperatorSnapshot:
        if self._session is not None:
            raise P8Error("OPERATOR_SESSION_ALREADY_REGISTERED")
        if self.mode is Mode.PARTICIPANT:
            require_participant_ready(self.readiness, source_kind="REAL")
        expected_route_id = (
            None
            if self.runtime.module_exit is None
            else self.runtime.module_exit.route_id
        )
        if expected_route_id is not None and request.route_id != expected_route_id:
            raise P8Error("REGISTRATION_EXIT_ROUTE_MISMATCH")
        assignment = claim_registration(self.root, request, self.mode)
        services = SessionServices(
            self.root,
            assignment,
            request.run_id,
            self.mode,
            provenance="ACTUAL_BOUNDED_OPERATOR",
            monotonic_ns=self._monotonic_ns,
            utc_ns=self._utc_ns,
            session_type=request.session_type,
        )

        def create_components(
            owned_services: SessionServices, occurrences: OccurrenceRegistry
        ) -> OperatorComponents:
            components = OperatorComponents(
                owned_services,
                occurrences,
                self.runtime,
                self._handoff_nominal_m2,
            )
            self._components = components
            return components

        session = ExperimentSession(
            services,
            self.readiness,
            OccurrenceRegistry(
                self.root / "_dev_occurrences" / "_pilot_runs" / request.run_id
                if request.session_type is not None
                else self.root / "_dev_occurrences"
            ),
            create_components,
        )
        self._session = session
        session.initialize()
        return self.snapshot()

    def record_actual(self, receipt: Receipt) -> OperatorSnapshot:
        if receipt.name == "surt" and receipt.status == "OPERATOR_CONFIRMED":
            raise P8Error("SURT_UNCONNECTED_CANNOT_BE_CONFIRMED_REAL")
        if (
            receipt.name == "calibration_boundary"
            and receipt.status == "OPERATOR_CONFIRMED"
        ):
            raise P8Error("CALIBRATION_REQUIRES_EXTERNAL_UNCONNECTED_STATUS")
        self.session.acknowledge(receipt.name, receipt.status, receipt.value)
        return self.snapshot()

    def advance(self) -> OperatorSnapshot | DoneClaim:
        session = self.session
        if session.state in (Stage.SESSION_COMPLETE, Stage.ABORTED):
            raise P8Error("TERMINAL_SESSION")
        target = SEQUENCE[SEQUENCE.index(session.state) + 1]
        session.transition(target)
        if session.closed:
            return self._done_claim()
        return self.snapshot()

    def execute_training(
        self, training: TrainingRun, duration: float
    ) -> ModuleRunResult:
        if any(
            result.stage is self.session.state
            for result in self._operator_components().runs
        ):
            raise P8Error("ONE_ACTUAL_BOUNDED_RUN_PER_STAGE")
        try:
            result = self._operator_components().execute_training(training, duration)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._record_runtime_fault(error)
            raise
        return self._handle_run_result(result)

    def execute_current_module(self, duration: float | None = None) -> ModuleRunResult:
        if any(
            result.stage is self.session.state
            for result in self._operator_components().runs
        ):
            raise P8Error("ONE_ACTUAL_BOUNDED_RUN_PER_STAGE")
        try:
            result = self._operator_components().execute_current_module(duration)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._record_runtime_fault(error)
            partial = next((run for run in self._operator_components().runs
                            if run.stage is self.session.state
                            and run.completion_reason == "HAZARD_COLLISION"
                            and run.collision_stop is not None
                            and run.collision_stop.get("confirmation") is not None), None)
            if partial is not None:
                self._operator_components().runtime_faults[-1]["status"] = "COLLISION_END_FINALIZATION_ERROR"
                return self._handle_run_result(partial)
            raise
        return self._handle_run_result(result)

    def _record_runtime_fault(self, error):
        if isinstance(error, TsxwDeviceError) and str(error) == CANCELLED:
            self.session.abort("OPERATOR_INPUT_PREPARATION_CANCELLED")
            return
        components = self._operator_components()
        record = {
            "error": f"{type(error).__name__}:{error}",
            "stage": self.session.state.value,
            "status": "AWAITING_OPERATOR_RUNTIME_UNAVAILABLE",
            "analysis_eligible": False,
        }
        components.runtime_faults.append(record)
        try:
            components.services.emit("operator_runtime_unavailable", record)
        except Exception as failure:  # noqa: BLE001 -- keep evidence when storage also fails
            record["recording_error"] = str(failure)

    def approve_dev_outcome(self, outcome: DevOutcome, reason: str) -> OperatorSnapshot:
        stage = self.session.state
        if stage in (
            Stage.TRAINING,
            Stage.MODULE_1_RUNNING,
            Stage.MODULE_2_RUNNING,
        ) and not any(
            result.stage is stage for result in self._operator_components().runs
        ):
            raise P8Error("DEV_PARTIAL_REQUIRES_ACTUAL_BOUNDED_RUN")
        self.session.approve_dev_outcome(outcome.value, reason)
        return self.snapshot()

    def record_execution_policy_outcome(
        self, outcome: DevOutcome, reason: str
    ) -> OperatorSnapshot:
        stage = self.session.state
        if stage is Stage.TRAINING and not any(
            result.stage is stage and result.actual_active_drive_s > 0.0
            for result in self._operator_components().runs
        ):
            raise P8Error("POSITIVE_ACTUAL_TRAINING_RUN_REQUIRED")
        if stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING) and not any(
            result.stage is stage for result in self._operator_components().runs
        ):
            raise P8Error("EXECUTION_POLICY_PARTIAL_REQUIRES_ACTUAL_BOUNDED_RUN")
        self.session.record_execution_policy_outcome(outcome.value, reason)
        return self.snapshot()

    def abort(self, reason: str) -> DoneClaim:
        self.session.abort(reason)
        return self._done_claim()

    def snapshot(self) -> OperatorSnapshot:
        session = self.session
        components = self._operator_components()
        post = components.post
        hazard = components.hazard_observation
        return OperatorSnapshot(
            stage=session.state,
            participant_id=str(session.services.context.participant_id),
            run_id=str(session.services.context.study_run_id),
            stage_elapsed_s=(
                session.services.elapsed - session.records[-1]["start_session_s"]
            ),
            closed=session.closed,
            allowed_receipts=tuple(RECEIPTS.get(session.state, ())),
            receipts=dict(session.receipts),
            stage_approval=session.records[-1].get("dev_approval"),
            module_runs=tuple(components.runs),
            analysis_eligible=bool(post["analysis_eligible"]),
            deletion_status=str(post["deletion_status"]),
            result_export=session.result_export,
            module_2_condition=session.services.context.module_2_condition,
            hazard_status=(
                None
                if hazard is None
                else f"{hazard.get('execution_status', 'NOT_RECORDED')} / "
                f"{hazard.get('hazard_state', 'NOT_RECORDED')}"
            ),
        )

    def _operator_components(self) -> OperatorComponents:
        if self._components is None:
            raise P8Error("OPERATOR_COMPONENTS_UNAVAILABLE")
        return self._components

    def _handle_run_result(self, result: ModuleRunResult) -> ModuleRunResult:
        if result.completion_reason == "HAZARD_COLLISION":
            # The partial module and collision evidence have already been stored.
            # Preserve the incomplete protocol status, with a specific intended end reason.
            self.session.abort("HAZARD_COLLISION")
            return result
        if result.user_exited:
            self.session.abort("USER_EXIT")
            raise P8Error("USER_EXIT_ABORTED_OPERATING_SESSION")
        return result

    def _handoff_nominal_m2(self) -> RuntimeFrameContinuation | None:
        session = self.session
        if session.state is not Stage.MODULE_2_RUNNING:
            raise P8Error("NOMINAL_M2_HANDOFF_REQUIRES_MODULE_2_RUNNING")
        session.record_execution_policy_outcome(
            DevOutcome.PARTIAL.value,
            "nominal M2 viewer window completed; final reduction follows live handoff",
        )
        session.transition(Stage.FINAL_HAZARD)
        components = self._operator_components()
        continuation_prepared = components.hazard_loop is not None
        session.record_execution_policy_outcome(
            DevOutcome.BLOCKED.value,
            (
                "offline P7 continuation prepared with injected test dependencies; "
                "physical hazard execution remains blocked"
                if continuation_prepared
                else "moving hazard policy and physical readiness blocked; not run"
            ),
        )
        return components.hazard_loop

    def _done_claim(self) -> DoneClaim:
        result = self.session.result
        return DoneClaim(
            completion_status=str(result["completion_status"]),
            stage=self.session.state,
            participant_ready=False,
            cleanup_completed=bool(self.session.cleanup_report["completed"]),
            manifest_path=self.session.services.directory / "session_manifest.json",
        )
