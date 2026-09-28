import csv
import json
from dataclasses import replace
from pathlib import Path

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
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    ExternalClockTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.logging.csv_logger import (
    RESEARCH_EVENT_HEADER,
    RESEARCH_TELEMETRY_HEADER,
    ResearchCsvError,
    ResearchCsvLogger,
    ResearchEvent,
)
from src.vehicle import VehicleState


def _study_run() -> StudyRunContext:
    return StudyRunContext(
        study_run_id=StudyRunId("run-001"),
        participant_id=ParticipantId("participant-007"),
        module_1_condition=Module1Condition.SURT,
        module_2_condition=Module2Condition.MANUAL,
    )


def _segment(study_run_id: StudyRunId | None = None) -> SegmentContext:
    return SegmentContext(
        segment_id=SegmentId("segment-m1"),
        study_run_id=study_run_id or StudyRunId("run-001"),
        phase=ExperimentPhase.MODULE_1,
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.SURT,
        outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
    )


def _timestamp() -> TimestampEnvelope:
    return TimestampEnvelope(
        host=HostClockTimestamp(
            monotonic_ns=125_000_000,
            utc_ns=1_790_000_000_000_000_000,
        ),
        carla_snapshot=CarlaSnapshotTimestamp(
            simulation_seconds=12.5,
            frame=750,
            host_capture_started_monotonic_ns=124_900_000,
            host_capture_completed_monotonic_ns=124_950_000,
        ),
        external_clocks=(
            ExternalClockTimestamp(
                source="eye_tracker",
                raw_value="000123456789",
                unit="device_tick",
            ),
        ),
    )


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as csv_file:
        return list(csv.reader(csv_file))


def test_research_csv_logger_creates_distinct_files_with_ordered_headers(tmp_path):
    common_header = (
        "study_run_id",
        "participant_id",
        "module_1_condition",
        "module_2_condition",
        "segment_id",
        "phase",
        "module",
        "block",
        "condition",
        "order",
        "route",
        "outcome_family",
        "host_monotonic_ns",
        "host_utc_ns",
        "carla_simulation_seconds",
        "carla_frame",
        "carla_capture_started_host_monotonic_ns",
        "carla_capture_completed_host_monotonic_ns",
        "external_timestamps_json",
    )
    logger = ResearchCsvLogger(_study_run(), tmp_path)

    logger.close()

    assert RESEARCH_TELEMETRY_HEADER == common_header + (
        "vehicle_state_timestamp_legacy",
        "speed_kmh",
        "steering",
        "throttle",
        "brake",
        "lane_id",
        "indicator",
    )
    assert RESEARCH_EVENT_HEADER == common_header + ("event_type", "event_value")
    assert logger.telemetry_path == tmp_path / "research_telemetry_run-001.csv"
    assert logger.event_path == tmp_path / "research_events_run-001.csv"
    assert _read_rows(logger.telemetry_path) == [list(RESEARCH_TELEMETRY_HEADER)]
    assert _read_rows(logger.event_path) == [list(RESEARCH_EVENT_HEADER)]


def test_research_csv_logger_serializes_context_timestamp_and_vehicle_state(tmp_path):
    logger = ResearchCsvLogger(_study_run(), tmp_path)

    logger.write_telemetry(
        replace(
            _segment(),
            block="block-alpha",
            order="module-1-first",
            route="route-north",
        ),
        _timestamp(),
        VehicleState(
            timestamp=999.0,
            speed_kmh=80.0,
            steering=-0.1,
            throttle=0.4,
            brake=0.0,
            lane_id=2,
            indicator="left",
        ),
    )
    logger.close()

    row = dict(zip(RESEARCH_TELEMETRY_HEADER, _read_rows(logger.telemetry_path)[1]))
    assert row["study_run_id"] == "run-001"
    assert row["participant_id"] == "participant-007"
    assert row["module_1_condition"] == "SURT"
    assert row["module_2_condition"] == "MANUAL"
    assert row["segment_id"] == "segment-m1"
    assert row["phase"] == "MODULE_1"
    assert row["module"] == "MODULE_1"
    assert row["block"] == "block-alpha"
    assert row["condition"] == "SURT"
    assert row["order"] == "module-1-first"
    assert row["route"] == "route-north"
    assert row["outcome_family"] == "AUTOMATION_CHOICE"
    assert row["host_monotonic_ns"] == "125000000"
    assert row["host_utc_ns"] == "1790000000000000000"
    assert row["carla_simulation_seconds"] == "12.5"
    assert row["carla_frame"] == "750"
    assert row["carla_capture_started_host_monotonic_ns"] == "124900000"
    assert row["carla_capture_completed_host_monotonic_ns"] == "124950000"
    assert json.loads(row["external_timestamps_json"]) == [
        {
            "source": "eye_tracker",
            "raw_value": "000123456789",
            "unit": "device_tick",
        }
    ]
    assert row["vehicle_state_timestamp_legacy"] == "999.0"
    assert row["speed_kmh"] == "80.0"
    assert row["steering"] == "-0.1"
    assert row["throttle"] == "0.4"
    assert row["brake"] == "0.0"
    assert row["lane_id"] == "2"
    assert row["indicator"] == "left"


def test_research_csv_logger_leaves_unavailable_timestamps_blank(tmp_path):
    logger = ResearchCsvLogger(_study_run(), tmp_path)

    logger.write_event(
        _segment(),
        TimestampEnvelope(host=HostClockTimestamp(monotonic_ns=10, utc_ns=20)),
        ResearchEvent(event_type="segment_started"),
    )
    logger.close()

    row = dict(zip(RESEARCH_EVENT_HEADER, _read_rows(logger.event_path)[1]))
    assert row["block"] == ""
    assert row["order"] == ""
    assert row["route"] == ""
    assert row["carla_simulation_seconds"] == ""
    assert row["carla_frame"] == ""
    assert row["carla_capture_started_host_monotonic_ns"] == ""
    assert row["carla_capture_completed_host_monotonic_ns"] == ""
    assert row["external_timestamps_json"] == ""
    assert row["event_type"] == "segment_started"
    assert row["event_value"] == ""


def test_research_csv_logger_rejects_segment_from_another_study_run(tmp_path):
    logger = ResearchCsvLogger(_study_run(), tmp_path)

    with pytest.raises(ResearchCsvError, match="study run"):
        logger.write_event(
            _segment(StudyRunId("run-002")),
            _timestamp(),
            ResearchEvent(event_type="segment_started"),
        )

    logger.close()
