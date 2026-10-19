from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import assert_never

from src.experiment.assignment import ParticipantAssignment
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import Json
from src.p8.component_contract import SessionComponents
from src.p8.components import DevelopmentComponents
from src.p8.manifest import inspect_outputs
from src.p8.model import RECEIPTS, SEQUENCE, Mode, P8Error, Stage
from src.p8.operator_results import OperatorResultExport, export_operator_results
from src.p8.readiness import Readiness, require_participant_ready
from src.p8.services import SessionServices


class ExperimentSession:
    def __init__(
        self,
        services: SessionServices,
        readiness: Readiness,
        occurrences: OccurrenceRegistry,
        component_factory: Callable[
            [SessionServices, OccurrenceRegistry], SessionComponents
        ] = DevelopmentComponents,
    ) -> None:
        self.services, self.readiness, self.occurrences = (
            services,
            readiness,
            occurrences,
        )
        self.state = Stage.SESSION_CREATED
        self.components: SessionComponents | None = None
        self._component_factory = component_factory
        self.closed = False
        self.records: list[dict[str, Json]] = []
        self.receipts: dict[str, str] = {}
        self.abort_reason: str | None = None
        self.cleanup_report: dict[str, Json] = {
            "completed": False,
            "order": [],
            "errors": [],
        }
        self.initial_components: dict[str, Json] = {}
        self.result: dict[str, Json] = {}
        self.result_export: OperatorResultExport | None = None
        self._lifecycle_errors: list[str] = []
        self._begin_record()
        self._save("RUNNING")

    def initialize(self) -> None:
        try:
            if self.services.mode is Mode.PARTICIPANT:
                require_participant_ready(self.readiness, source_kind="REAL")
            self.components = self._component_factory(self.services, self.occurrences)
            self.initial_components = self.components.snapshot()
            self.services.emit(
                "assignment",
                {
                    "study_context": asdict(self.services.context),
                    "assignment_policy": "P4_ROW_PRESERVED_OR_EXPLICIT_DEV_OVERRIDE",
                },
            )
            self._save("RUNNING")
        except (RuntimeError, ValueError, OSError) as error:
            self.abort(str(error), blocked=self.services.mode is Mode.PARTICIPANT)
            raise

    def acknowledge(self, name: str, status: str, value: Json = None) -> None:
        if self.closed or name not in RECEIPTS.get(self.state, ()):
            raise P8Error("RECEIPT_NOT_ALLOWED_FOR_STAGE")
        if status not in (
            "DEV_STUB",
            "OPERATOR_CONFIRMED",
            "NOT_IMPLEMENTED",
            "EXTERNAL_OPERATOR_STEP",
            "REFUSED",
        ):
            raise P8Error("UNKNOWN_RECEIPT_STATUS")
        if status == "DEV_STUB" and self.services.mode is Mode.PARTICIPANT:
            raise P8Error("PARTICIPANT_CANNOT_USE_DEV_STUB")
        self.receipts[name] = status
        self.records[-1]["receipts"] = dict(self.receipts)
        values = self.records[-1].setdefault("receipt_values", {})
        values[name] = value
        self.services.emit(
            name + "_status",
            {
                "status": status,
                "value": value,
                "real_completion_inferred": False,
            },
        )
        if self.components is not None:
            self.components.record_receipt(name, status, value)
        self._save("RUNNING")

    def approve_dev_outcome(self, outcome: str, reason: str) -> None:
        if self.services.mode is not Mode.DEV_REHEARSAL:
            raise P8Error("DEV_OUTCOME_APPROVAL_REQUIRES_REHEARSAL_MODE")
        if self.state in (
            Stage.SESSION_CREATED,
            Stage.REST,
            Stage.SESSION_COMPLETE,
            Stage.ABORTED,
        ):
            raise P8Error("DEV_OUTCOME_APPROVAL_NOT_ALLOWED_FOR_STAGE")
        if not reason.strip():
            raise P8Error("DEV_OUTCOME_REQUIRES_REASON")
        if self.state is Stage.FINAL_HAZARD:
            if outcome != "BLOCKED":
                raise P8Error("FINAL_HAZARD_OUTCOME_MUST_BE_BLOCKED")
        elif outcome not in ("PARTIAL", "SKIPPED"):
            raise P8Error("DEV_OUTCOME_REQUIRES_PARTIAL_OR_SKIPPED_AND_REASON")
        approval: dict[str, Json] = {
            "outcome": outcome,
            "reason": reason,
            "operator_approved": True,
        }
        self.records[-1]["dev_approval"] = approval
        self.services.emit("dev_stage_outcome_approval", approval)
        self._save("RUNNING")

    def record_execution_policy_outcome(self, outcome: str, reason: str) -> None:
        if (
            self.services.mode is not Mode.DEV_REHEARSAL
            or self.components is None
            or self.components.kind != "ACTUAL_BOUNDED_OPERATOR"
        ):
            raise P8Error("EXECUTION_POLICY_OUTCOME_REQUIRES_OPERATOR_REHEARSAL")
        if outcome not in ("PARTIAL", "SKIPPED", "BLOCKED") or not reason.strip():
            raise P8Error("EXECUTION_POLICY_OUTCOME_REQUIRES_REASON")
        match self.state:
            case Stage.CONSENT_AND_ELIGIBILITY | Stage.POST_EXPERIMENT:
                if outcome != "SKIPPED":
                    raise P8Error("EXECUTION_POLICY_OUTCOME_NOT_ALLOWED_FOR_STAGE")
                scope: dict[str, Json] = {
                    "external_management": True,
                    "real_completion_inferred": False,
                    "completion_unverified": True,
                    "software_collection_performed": False,
                }
            case Stage.TRAINING:
                if outcome != "PARTIAL":
                    raise P8Error("EXECUTION_POLICY_OUTCOME_NOT_ALLOWED_FOR_STAGE")
                blocker = self.components.completion_blocker(Stage.TRAINING)
                if blocker is not None:
                    raise P8Error(blocker)
                scope = {
                    "external_management": True,
                    "real_completion_inferred": False,
                    "completion_unverified": True,
                    "skill_assessment": "EXTERNAL_UNVERIFIED",
                    "p5_surt_connected": False,
                }
            case Stage.MODULE_1_RUNNING | Stage.MODULE_2_RUNNING:
                if outcome != "PARTIAL":
                    raise P8Error("EXECUTION_POLICY_OUTCOME_NOT_ALLOWED_FOR_STAGE")
                scope = {
                    "external_management": False,
                    "real_completion_inferred": False,
                    "completion_unverified": True,
                }
            case Stage.FINAL_HAZARD:
                if outcome != "BLOCKED":
                    raise P8Error("EXECUTION_POLICY_OUTCOME_NOT_ALLOWED_FOR_STAGE")
                scope = {
                    "external_management": False,
                    "real_completion_inferred": False,
                    "completion_unverified": True,
                }
            case (
                Stage.SESSION_CREATED
                | Stage.EYE_TRACKER_CALIBRATION
                | Stage.MODULE_1_PREPARE
                | Stage.MODULE_1_COMPLETE
                | Stage.REST
                | Stage.MODULE_2_PREPARE
                | Stage.SESSION_COMPLETE
                | Stage.ABORTED
            ):
                raise P8Error("EXECUTION_POLICY_OUTCOME_NOT_ALLOWED_FOR_STAGE")
            case unreachable:
                assert_never(unreachable)
        policy: dict[str, Json] = {
            "outcome": outcome,
            "reason": reason,
            "origin": "execution_policy",
            "operator_approved": False,
            **scope,
        }
        self.records[-1]["execution_policy_outcome"] = policy
        self.services.emit("execution_policy_stage_outcome", policy)
        self._save("RUNNING")

    def transition(self, target: Stage) -> None:
        if self.closed or self.state in (Stage.ABORTED, Stage.SESSION_COMPLETE):
            raise P8Error("TERMINAL_SESSION")
        index = SEQUENCE.index(self.state)
        if target is not SEQUENCE[index + 1]:
            raise P8Error("STAGE_ORDER_VIOLATION")
        self._guard()
        if self.components is None:
            raise P8Error("COMPONENTS_UNAVAILABLE")
        try:
            self.components.leave(self.state)
            approval = self.records[-1].get("dev_approval")
            policy = self.records[-1].get("execution_policy_outcome")
            outcome = approval if approval is not None else policy
            self._end_record(
                "DEV_COMPLETED" if outcome is None else str(outcome["outcome"]),
                "STAGE_CONTRACT_SATISFIED"
                if outcome is None
                else str(outcome["reason"]),
            )
            self.state = self.services.stage = target
            self.receipts = {}
            self._begin_record(started=False)
            self.components.enter(target)
            self._activate_record()
            if target is Stage.SESSION_COMPLETE:
                self._end_record(
                    "DEV_COMPLETED", "DEV_SEQUENCE_ENDED_NOT_PARTICIPANT_COMPLETE"
                )
                self._finalize(
                    "DEV_REHEARSAL_PARTIAL_COMPLETE"
                    if self.services.mode is Mode.DEV_REHEARSAL
                    else "DEV_FLOW_COMPLETE"
                )
            else:
                self._save("RUNNING")
        except (RuntimeError, ValueError, OSError) as error:
            if not self.closed:
                self.abort(str(error))
            raise

    def abort(self, reason: str, *, blocked: bool = False) -> None:
        if self.closed:
            return
        self.abort_reason = reason
        try:
            self._end_record("ABORTED", reason)
        except (RuntimeError, ValueError, OSError) as error:
            self._lifecycle_errors.append("ABORT_EVENT:" + str(error))
        self.state = self.services.stage = Stage.ABORTED
        try:
            self._begin_record()
        except (RuntimeError, ValueError, OSError) as error:
            self._lifecycle_errors.append("ABORT_START:" + str(error))
        try:
            self._end_record("ABORTED", reason)
        except (RuntimeError, ValueError, OSError) as error:
            self._lifecycle_errors.append("ABORT_END:" + str(error))
        self._finalize(
            "PARTICIPANT_BLOCKED" if blocked else
            "ENDED_AFTER_HAZARD_COLLISION" if reason == "HAZARD_COLLISION" else "ABORTED"
        )

    def _guard(self) -> None:
        approval = self.records[-1].get("dev_approval")
        policy = self.records[-1].get("execution_policy_outcome")
        if policy is not None:
            if self.state is Stage.TRAINING:
                if self.components is None:
                    raise P8Error("COMPONENTS_UNAVAILABLE")
                blocker = self.components.completion_blocker(Stage.TRAINING)
                if blocker is not None:
                    raise P8Error(blocker)
            return
        if approval is not None:
            return
        required = RECEIPTS.get(self.state, ())
        if any(not self._receipt_satisfied(item) for item in required):
            raise P8Error("STAGE_RECEIPT_REQUIRED")
        elapsed = self.services.elapsed - self.records[-1]["start_session_s"]
        minimum_s = self.services.profile.minimum(self.state)
        if (
            self.state is Stage.REST
            and self.components is not None
            and self.components.kind == "ACTUAL_BOUNDED_OPERATOR"
        ):
            minimum_s = 0
        if elapsed < minimum_s:
            raise P8Error("STAGE_MINIMUM_DURATION_NOT_REACHED")
        if self.components is None:
            raise P8Error("COMPONENTS_UNAVAILABLE")
        blocker = self.components.completion_blocker(self.state)
        if blocker is not None:
            raise P8Error(blocker)

    def _receipt_satisfied(self, name: str) -> bool:
        status = self.receipts.get(name)
        if status in ("DEV_STUB", "OPERATOR_CONFIRMED"):
            return True
        if self.services.mode is not Mode.DEV_REHEARSAL:
            return False
        if self.state is Stage.EYE_TRACKER_CALIBRATION:
            return status == "EXTERNAL_OPERATOR_STEP"
        if self.state is Stage.POST_EXPERIMENT:
            return status in ("NOT_IMPLEMENTED", "EXTERNAL_OPERATOR_STEP", "REFUSED")
        return False

    def _begin_record(self, *, started: bool = True) -> None:
        segment = self.services.segment()
        minimum_s = self.services.profile.minimum(self.state)
        record: dict[str, Json] = {
            "stage": self.state.value,
            "status": "RUNNING" if started else "ENTERING",
            "entry_attempt_session_s": self.services.elapsed,
            "start_session_s": self.services.elapsed if started else None,
            "end_session_s": None,
            "duration_s": None,
            "condition": None if segment.condition is None else segment.condition.value,
            "minimum_s": minimum_s,
            "receipts": {},
            "receipt_values": {},
        }
        if (
            self.state is Stage.REST
            and self.components is not None
            and self.components.kind == "ACTUAL_BOUNDED_OPERATOR"
        ):
            record["minimum_s"] = 0
            record["recommended_duration_s"] = minimum_s
        self.records.append(record)
        self.services.emit(
            "stage_start" if started else "stage_start_attempt",
            {"stage": self.state.value},
        )

    def _activate_record(self) -> None:
        self.records[-1].update(
            {"status": "RUNNING", "start_session_s": self.services.elapsed}
        )
        self.services.emit("stage_start", {"stage": self.state.value})

    def _end_record(self, status: str, reason: str) -> None:
        record = self.records[-1]
        if record["end_session_s"] is not None:
            return
        end_session_s = self.services.elapsed
        start_session_s = record["start_session_s"]
        truthful_rest_timing = (
            self.state is Stage.REST
            and self.components is not None
            and self.components.kind == "ACTUAL_BOUNDED_OPERATOR"
        )
        record.update(
            {
                "status": status,
                "end_session_s": end_session_s,
                "duration_s": None
                if start_session_s is None
                else (end_session_s if truthful_rest_timing else self.services.elapsed)
                - start_session_s,
                "reason": reason,
            }
        )
        self.services.emit(
            "stage_abort" if status == "ABORTED" else "stage_end", record
        )

    def _finalize(self, status: str) -> None:
        errors, order = list(self._lifecycle_errors), []
        if self.components is not None:
            try:
                self.components.flush()
            except (RuntimeError, ValueError, OSError) as error:
                errors.append("FLUSH:" + str(error))
        order.append("LOGGER_FLUSH")
        try:
            self.services.emit(
                "p8_logs_flushed_before_cleanup", {"errors": list(errors)}
            )
        except (RuntimeError, ValueError, OSError) as error:
            errors.append("FLUSH_EVENT:" + str(error))
        try:
            if self.components is not None:
                self.components.cleanup()
        except (RuntimeError, ValueError, OSError) as error:
            errors.append("CLEANUP:" + str(error))
        order.append("OWNED_RESOURCE_CLEANUP")
        try:
            self.services.emit("p8_cleanup_end", {"errors": errors})
        except (RuntimeError, ValueError, OSError) as error:
            errors.append("CLEANUP_EVENT:" + str(error))
        try:
            self.services.close()
        except OSError as error:
            errors.append("LOGGER_CLOSE:" + str(error))
        if errors:
            status = "ABORTED"
            self.abort_reason = self.abort_reason or ";".join(errors)
            self.state = Stage.ABORTED
        order.append("SESSION_STATUS_PERSIST")
        self.cleanup_report = {
            "completed": not errors,
            "order": order,
            "errors": errors,
        }
        self.closed = True
        snapshot = {} if self.components is None else self.components.snapshot()
        complete = status == "DEV_FLOW_COMPLETE"
        try:
            audit = inspect_outputs(
                self.services.directory,
                self.services.context,
                complete=complete,
                components=snapshot,
                provenance=self.services.provenance,
            )
        except (OSError, ValueError, KeyError) as error:
            audit = {
                "software_artifacts_complete": False,
                "issues": ["AUDIT_ERROR:" + str(error)],
            }
        if complete and not audit["software_artifacts_complete"]:
            status, self.abort_reason, self.state = (
                "ABORTED",
                "OUTPUT_COMPLETENESS_FAILED",
                Stage.ABORTED,
            )
        try:
            self._save(status, audit=audit)
        finally:
            if self.services.provenance == "ACTUAL_BOUNDED_OPERATOR":
                from src.p8.collection_report import finalize_collection

                finalize_collection(self.services.directory)
                self.result_export = export_operator_results(
                    self.services.directory,
                    closed=self.services.closed,
                )

    def _save(self, status: str, *, audit: dict[str, Json] | None = None) -> None:
        self.result = {
            **self.services.manifest_base(),
            "state": self.state.value,
            "completion_status": status,
            "abort_reason": self.abort_reason,
            "assigned_conditions": {
                "m1": self.services.context.module_1_condition.value,
                "m2": self.services.context.module_2_condition.value,
            },
            "readiness": asdict(self.readiness),
            "stages": self.records,
            "components": {} if self.components is None else self.components.snapshot(),
            "initial_components": self.initial_components,
            "cleanup": self.cleanup_report,
            "hazard_occurrence_store": str(self.occurrences.directory),
            "hazard_occurrence_store_scope": "DEV_ONLY_SEPARATE_FROM_PARTICIPANT_STORE",
            "completeness": audit
            or {"software_artifacts_complete": False, "issues": ["RUN_IN_PROGRESS"]},
        }
        self.services.save_manifest(self.result)


class SessionManager:
    def __init__(
        self,
        root: Path,
        *,
        mode: Mode = Mode.ACCELERATED,
        readiness: Readiness | None = None,
        component_factory: Callable[
            [SessionServices, OccurrenceRegistry], SessionComponents
        ] = DevelopmentComponents,
    ) -> None:
        self.root, self.mode, self.readiness = root, mode, readiness or Readiness()
        self.component_factory = component_factory
        self.active: ExperimentSession | None = None
        self.occurrences = OccurrenceRegistry(root / "_dev_occurrences")

    def create(
        self, assignment: ParticipantAssignment, run_id: str
    ) -> ExperimentSession:
        if self.mode is Mode.DEV_LIVE_DRIVE:
            raise P8Error("DEV_LIVE_REQUIRES_DEDICATED_RUNNER")
        if self.active is not None and (
            not self.active.closed or not self.active.cleanup_report["completed"]
        ):
            raise P8Error("PREVIOUS_PARTICIPANT_NOT_CLOSED_OR_CLEANED")
        services = SessionServices(self.root, assignment, run_id, self.mode)
        self.active = ExperimentSession(
            services,
            replace(self.readiness, eye_tracker_calibrated=False),
            self.occurrences,
            self.component_factory,
        )
        self.active.initialize()
        return self.active
