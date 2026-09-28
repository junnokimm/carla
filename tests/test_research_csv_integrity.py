import csv

import pytest

from src.experiment.context import (
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
)
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchCsvError, ResearchCsvLogger, ResearchEvent


def _study_run() -> StudyRunContext:
    return StudyRunContext(
        study_run_id=StudyRunId("run-001"),
        participant_id=ParticipantId("participant-007"),
        module_1_condition=Module1Condition.SURT,
        module_2_condition=Module2Condition.MANUAL,
    )


def _timestamp() -> TimestampEnvelope:
    return TimestampEnvelope(host=HostClockTimestamp(monotonic_ns=1, utc_ns=2))


def _module_1_segment() -> SegmentContext:
    return SegmentContext(
        segment_id=SegmentId("segment-m1"),
        study_run_id=StudyRunId("run-001"),
        phase=ExperimentPhase.MODULE_1,
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.SURT,
        outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
    )


def test_research_csv_logger_rejects_reinitialization_without_changing_files(
    tmp_path,
):
    logger = ResearchCsvLogger(_study_run(), tmp_path)
    logger.write_event(
        _module_1_segment(),
        _timestamp(),
        ResearchEvent(event_type="segment_started"),
    )
    logger.close()
    telemetry_before = logger.telemetry_path.read_bytes()
    event_before = logger.event_path.read_bytes()

    with pytest.raises(ResearchCsvError, match="already exists"):
        ResearchCsvLogger(_study_run(), tmp_path)

    assert logger.telemetry_path.read_bytes() == telemetry_before
    assert logger.event_path.read_bytes() == event_before


@pytest.mark.parametrize(
    ("existing_name", "absent_name"),
    (
        ("research_telemetry_run-001.csv", "research_events_run-001.csv"),
        ("research_events_run-001.csv", "research_telemetry_run-001.csv"),
    ),
)
def test_research_csv_logger_rejects_one_existing_file_without_creating_the_other(
    tmp_path,
    existing_name,
    absent_name,
):
    existing_path = tmp_path / existing_name
    absent_path = tmp_path / absent_name
    existing_path.write_text("existing research data", encoding="utf-8")

    with pytest.raises(ResearchCsvError, match="already exists"):
        ResearchCsvLogger(_study_run(), tmp_path)

    assert existing_path.read_text(encoding="utf-8") == "existing research data"
    assert not absent_path.exists()


@pytest.mark.parametrize(
    ("phase", "module", "condition", "outcome_family"),
    (
        (
            ExperimentPhase.MODULE_1,
            ExperimentModule.MODULE_1,
            Module1Condition.NO_SURT,
            OutcomeFamily.AUTOMATION_CHOICE,
        ),
        (
            ExperimentPhase.MODULE_2,
            ExperimentModule.MODULE_2,
            Module2Condition.NOA_L2,
            OutcomeFamily.ATTENTION_ALLOCATION,
        ),
        (
            ExperimentPhase.FINAL_HAZARD_ASSESSMENT,
            ExperimentModule.MODULE_2,
            Module2Condition.NOA_L2,
            OutcomeFamily.HAZARD_RESPONSE,
        ),
    ),
)
def test_research_csv_logger_rejects_condition_not_assigned_to_study_run(
    tmp_path,
    phase,
    module,
    condition,
    outcome_family,
):
    logger = ResearchCsvLogger(_study_run(), tmp_path)
    segment = SegmentContext(
        segment_id=SegmentId("mismatched-segment"),
        study_run_id=StudyRunId("run-001"),
        phase=phase,
        module=module,
        condition=condition,
        outcome_family=outcome_family,
    )

    with pytest.raises(ResearchCsvError, match="assigned condition"):
        logger.write_event(segment, _timestamp(), ResearchEvent(event_type="started"))

    logger.close()


@pytest.mark.parametrize(
    ("phase", "module", "condition", "outcome_family"),
    (
        (
            ExperimentPhase.MODULE_1,
            ExperimentModule.MODULE_1,
            Module1Condition.SURT,
            OutcomeFamily.AUTOMATION_CHOICE,
        ),
        (
            ExperimentPhase.MODULE_2,
            ExperimentModule.MODULE_2,
            Module2Condition.MANUAL,
            OutcomeFamily.ATTENTION_ALLOCATION,
        ),
        (
            ExperimentPhase.FINAL_HAZARD_ASSESSMENT,
            ExperimentModule.MODULE_2,
            Module2Condition.MANUAL,
            OutcomeFamily.HAZARD_RESPONSE,
        ),
    ),
)
def test_research_csv_logger_records_condition_assigned_to_study_run(
    tmp_path,
    phase,
    module,
    condition,
    outcome_family,
):
    logger = ResearchCsvLogger(_study_run(), tmp_path)
    segment = SegmentContext(
        segment_id=SegmentId("assigned-segment"),
        study_run_id=StudyRunId("run-001"),
        phase=phase,
        module=module,
        condition=condition,
        outcome_family=outcome_family,
    )

    logger.write_event(segment, _timestamp(), ResearchEvent(event_type="started"))
    logger.close()

    with logger.event_path.open(newline="") as event_file:
        row = next(iter(csv.DictReader(event_file)))
    assert row["condition"] == condition.value
