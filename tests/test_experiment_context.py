from __future__ import annotations

from typing import assert_never

import pytest

from src.experiment.context import (
    EXPECTED_PHASE_ORDER,
    ExperimentContextError,
    ExperimentModule,
    ExperimentPhase,
    Module1Condition,
    Module2Condition,
    OutcomeFamily,
    ParticipantId,
    SegmentContext,
    SegmentId,
    StudyRunContext,
    StudyRunId,
    validate_segment_sequence,
)


def make_study_run() -> StudyRunContext:
    return StudyRunContext(
        study_run_id=StudyRunId("run-001"),
        participant_id=ParticipantId("participant-001"),
        module_1_condition=Module1Condition.SURT,
        module_2_condition=Module2Condition.NOA_L2,
    )


def make_segment(
    study_run: StudyRunContext,
    phase: ExperimentPhase,
) -> SegmentContext:
    match phase:
        case ExperimentPhase.MODULE_1:
            return SegmentContext(
                segment_id=SegmentId("segment-module-1"),
                study_run_id=study_run.study_run_id,
                phase=phase,
                module=ExperimentModule.MODULE_1,
                condition=study_run.module_1_condition,
                outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
            )
        case ExperimentPhase.MODULE_2:
            return SegmentContext(
                segment_id=SegmentId("segment-module-2"),
                study_run_id=study_run.study_run_id,
                phase=phase,
                module=ExperimentModule.MODULE_2,
                condition=study_run.module_2_condition,
                outcome_family=OutcomeFamily.ATTENTION_ALLOCATION,
            )
        case ExperimentPhase.FINAL_HAZARD_ASSESSMENT:
            return SegmentContext(
                segment_id=SegmentId("segment-hazard"),
                study_run_id=study_run.study_run_id,
                phase=phase,
                module=ExperimentModule.MODULE_2,
                condition=study_run.module_2_condition,
                outcome_family=OutcomeFamily.HAZARD_RESPONSE,
            )
        case (
            ExperimentPhase.CONSENT_AND_ELIGIBILITY
            | ExperimentPhase.TRAINING
            | ExperimentPhase.EYE_TRACKER_CALIBRATION
            | ExperimentPhase.REST
            | ExperimentPhase.POST_EXPERIMENT
        ):
            return SegmentContext(
                segment_id=SegmentId(f"segment-{phase.value.lower()}"),
                study_run_id=study_run.study_run_id,
                phase=phase,
                module=None,
                condition=None,
                outcome_family=None,
            )
        case unreachable:
            assert_never(unreachable)


def test_study_run_links_one_participant_to_distinct_module_conditions() -> None:
    study_run = make_study_run()

    assert study_run.participant_id == "participant-001"
    assert study_run.module_1_condition is Module1Condition.SURT
    assert study_run.module_2_condition is Module2Condition.NOA_L2


def test_segment_sequence_accepts_the_fixed_research_order() -> None:
    study_run = make_study_run()
    segments = tuple(make_segment(study_run, phase) for phase in EXPECTED_PHASE_ORDER)

    validated = validate_segment_sequence(study_run, segments)

    assert validated == segments


@pytest.mark.parametrize(
    ("phase", "module", "condition", "outcome_family"),
    [
        (
            ExperimentPhase.MODULE_1,
            ExperimentModule.MODULE_1,
            Module2Condition.MANUAL,
            OutcomeFamily.AUTOMATION_CHOICE,
        ),
        (
            ExperimentPhase.MODULE_2,
            ExperimentModule.MODULE_2,
            Module1Condition.NO_SURT,
            OutcomeFamily.ATTENTION_ALLOCATION,
        ),
    ],
)
def test_segment_rejects_a_condition_from_the_other_module(
    phase: ExperimentPhase,
    module: ExperimentModule,
    condition: Module1Condition | Module2Condition,
    outcome_family: OutcomeFamily,
) -> None:
    with pytest.raises(ExperimentContextError, match="condition"):
        SegmentContext(
            segment_id=SegmentId("invalid-condition"),
            study_run_id=StudyRunId("run-001"),
            phase=phase,
            module=module,
            condition=condition,
            outcome_family=outcome_family,
        )


def test_final_hazard_is_module_2_hazard_response() -> None:
    study_run = make_study_run()

    hazard = make_segment(study_run, ExperimentPhase.FINAL_HAZARD_ASSESSMENT)

    assert hazard.module is ExperimentModule.MODULE_2
    assert hazard.condition is study_run.module_2_condition
    assert hazard.outcome_family is OutcomeFamily.HAZARD_RESPONSE


def test_final_hazard_rejects_non_module_2_context() -> None:
    with pytest.raises(ExperimentContextError, match="FINAL_HAZARD_ASSESSMENT"):
        SegmentContext(
            segment_id=SegmentId("invalid-hazard"),
            study_run_id=StudyRunId("run-001"),
            phase=ExperimentPhase.FINAL_HAZARD_ASSESSMENT,
            module=None,
            condition=None,
            outcome_family=None,
        )


def test_segment_sequence_rejects_module_2_before_module_1() -> None:
    study_run = make_study_run()
    segments = [make_segment(study_run, phase) for phase in EXPECTED_PHASE_ORDER]
    module_1_index = EXPECTED_PHASE_ORDER.index(ExperimentPhase.MODULE_1)
    module_2_index = EXPECTED_PHASE_ORDER.index(ExperimentPhase.MODULE_2)
    segments[module_1_index], segments[module_2_index] = (
        segments[module_2_index],
        segments[module_1_index],
    )

    with pytest.raises(ExperimentContextError, match="segment order"):
        validate_segment_sequence(study_run, segments)


def test_segment_sequence_rejects_a_condition_not_assigned_to_the_study_run() -> None:
    study_run = make_study_run()
    segments = [make_segment(study_run, phase) for phase in EXPECTED_PHASE_ORDER]
    module_1_index = EXPECTED_PHASE_ORDER.index(ExperimentPhase.MODULE_1)
    segments[module_1_index] = SegmentContext(
        segment_id=SegmentId("segment-module-1"),
        study_run_id=study_run.study_run_id,
        phase=ExperimentPhase.MODULE_1,
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.NO_SURT,
        outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
    )

    with pytest.raises(ExperimentContextError, match="assigned condition"):
        validate_segment_sequence(study_run, segments)
