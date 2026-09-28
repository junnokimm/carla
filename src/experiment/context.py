from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NewType, assert_never

StudyRunId = NewType("StudyRunId", str)
ParticipantId = NewType("ParticipantId", str)
SegmentId = NewType("SegmentId", str)


class ExperimentModule(StrEnum):
    MODULE_1 = "MODULE_1"
    MODULE_2 = "MODULE_2"


class Module1Condition(StrEnum):
    NO_SURT = "NO_SURT"
    SURT = "SURT"


class Module2Condition(StrEnum):
    MANUAL = "MANUAL"
    NOA_L2 = "NOA_L2"


class ExperimentPhase(StrEnum):
    CONSENT_AND_ELIGIBILITY = "CONSENT_AND_ELIGIBILITY"
    TRAINING = "TRAINING"
    EYE_TRACKER_CALIBRATION = "EYE_TRACKER_CALIBRATION"
    MODULE_1 = "MODULE_1"
    REST = "REST"
    MODULE_2 = "MODULE_2"
    FINAL_HAZARD_ASSESSMENT = "FINAL_HAZARD_ASSESSMENT"
    POST_EXPERIMENT = "POST_EXPERIMENT"


class OutcomeFamily(StrEnum):
    AUTOMATION_CHOICE = "AUTOMATION_CHOICE"
    ATTENTION_ALLOCATION = "ATTENTION_ALLOCATION"
    HAZARD_RESPONSE = "HAZARD_RESPONSE"


ExperimentCondition = Module1Condition | Module2Condition

EXPECTED_PHASE_ORDER: Final = (
    ExperimentPhase.CONSENT_AND_ELIGIBILITY,
    ExperimentPhase.TRAINING,
    ExperimentPhase.EYE_TRACKER_CALIBRATION,
    ExperimentPhase.MODULE_1,
    ExperimentPhase.REST,
    ExperimentPhase.MODULE_2,
    ExperimentPhase.FINAL_HAZARD_ASSESSMENT,
    ExperimentPhase.POST_EXPERIMENT,
)


class ExperimentContextError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_nonblank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ExperimentContextError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class StudyRunContext:
    study_run_id: StudyRunId
    participant_id: ParticipantId
    module_1_condition: Module1Condition
    module_2_condition: Module2Condition

    def __post_init__(self) -> None:
        _require_nonblank(self.study_run_id, "study_run_id")
        _require_nonblank(self.participant_id, "participant_id")


@dataclass(frozen=True, slots=True)
class SegmentContext:
    segment_id: SegmentId
    study_run_id: StudyRunId
    phase: ExperimentPhase
    module: ExperimentModule | None
    condition: ExperimentCondition | None
    outcome_family: OutcomeFamily | None
    block: str | None = None
    order: str | None = None
    route: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.segment_id, "segment_id")
        _require_nonblank(self.study_run_id, "study_run_id")

        match self.phase:
            case ExperimentPhase.MODULE_1:
                valid = (
                    self.module is ExperimentModule.MODULE_1
                    and self.condition
                    in (Module1Condition.NO_SURT, Module1Condition.SURT)
                    and self.outcome_family is OutcomeFamily.AUTOMATION_CHOICE
                )
                if not valid:
                    raise ExperimentContextError(
                        "MODULE_1 requires its module 1 condition and AUTOMATION_CHOICE"
                    )
            case ExperimentPhase.MODULE_2:
                valid = (
                    self.module is ExperimentModule.MODULE_2
                    and self.condition
                    in (Module2Condition.MANUAL, Module2Condition.NOA_L2)
                    and self.outcome_family is OutcomeFamily.ATTENTION_ALLOCATION
                )
                if not valid:
                    raise ExperimentContextError(
                        "MODULE_2 requires its module 2 condition and ATTENTION_ALLOCATION"
                    )
            case ExperimentPhase.FINAL_HAZARD_ASSESSMENT:
                valid = (
                    self.module is ExperimentModule.MODULE_2
                    and self.condition
                    in (Module2Condition.MANUAL, Module2Condition.NOA_L2)
                    and self.outcome_family is OutcomeFamily.HAZARD_RESPONSE
                )
                if not valid:
                    raise ExperimentContextError(
                        "FINAL_HAZARD_ASSESSMENT requires MODULE_2, its assigned "
                        "module 2 condition, and HAZARD_RESPONSE"
                    )
            case (
                ExperimentPhase.CONSENT_AND_ELIGIBILITY
                | ExperimentPhase.TRAINING
                | ExperimentPhase.EYE_TRACKER_CALIBRATION
                | ExperimentPhase.REST
                | ExperimentPhase.POST_EXPERIMENT
            ):
                if any(
                    value is not None
                    for value in (self.module, self.condition, self.outcome_family)
                ):
                    raise ExperimentContextError(
                        f"{self.phase.value} must not define module research conditions"
                    )
            case unreachable:
                assert_never(unreachable)


def validate_segment_sequence(
    study_run: StudyRunContext,
    segments: Sequence[SegmentContext],
) -> tuple[SegmentContext, ...]:
    validated = tuple(segments)
    phases = tuple(segment.phase for segment in validated)
    if phases != EXPECTED_PHASE_ORDER:
        raise ExperimentContextError(
            "segment order must follow the fixed participant operation sequence"
        )

    segment_ids = {segment.segment_id for segment in validated}
    if len(segment_ids) != len(validated):
        raise ExperimentContextError("segment_id values must be unique within a study run")

    for segment in validated:
        if segment.study_run_id != study_run.study_run_id:
            raise ExperimentContextError(
                "every segment must belong to the validated study run"
            )

        match segment.phase:
            case ExperimentPhase.MODULE_1:
                if segment.condition is not study_run.module_1_condition:
                    raise ExperimentContextError(
                        "MODULE_1 segment must use the assigned condition"
                    )
            case (
                ExperimentPhase.MODULE_2
                | ExperimentPhase.FINAL_HAZARD_ASSESSMENT
            ):
                if segment.condition is not study_run.module_2_condition:
                    raise ExperimentContextError(
                        "MODULE_2 segments must use the assigned condition"
                    )
            case (
                ExperimentPhase.CONSENT_AND_ELIGIBILITY
                | ExperimentPhase.TRAINING
                | ExperimentPhase.EYE_TRACKER_CALIBRATION
                | ExperimentPhase.REST
                | ExperimentPhase.POST_EXPERIMENT
            ):
                continue
            case unreachable:
                assert_never(unreachable)

    return validated
