from __future__ import annotations

import csv
from pathlib import Path

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
from src.experiment.events import ResearchEventRecorder
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.logging.csv_logger import (
    RESEARCH_EVENT_HEADER,
    ResearchCsvLogger,
    ResearchEvent,
)
from src.vehicle import VehicleObservation, VehicleState


class FakeObservationSource:
    def __init__(self, observation: VehicleObservation) -> None:
        self.observation = observation
        self.call_count = 0

    def get_observation(self) -> VehicleObservation:
        self.call_count += 1
        return self.observation


class RecordingEventWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[SegmentContext, TimestampEnvelope, ResearchEvent]] = []
        self.closed = False

    def write_event(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        event: ResearchEvent,
    ) -> None:
        self.calls.append((segment, timestamp, event))

    def close(self) -> None:
        self.closed = True


def _study_run() -> StudyRunContext:
    return StudyRunContext(
        study_run_id=StudyRunId("run-001"),
        participant_id=ParticipantId("participant-007"),
        module_1_condition=Module1Condition.SURT,
        module_2_condition=Module2Condition.MANUAL,
    )


def _segment() -> SegmentContext:
    return SegmentContext(
        segment_id=SegmentId("segment-m1"),
        study_run_id=StudyRunId("run-001"),
        phase=ExperimentPhase.MODULE_1,
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.SURT,
        outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
    )


def _observation() -> VehicleObservation:
    return VehicleObservation(
        timestamp=TimestampEnvelope(
            host=HostClockTimestamp(monotonic_ns=1_250, utc_ns=1_700_000_000),
            carla_snapshot=CarlaSnapshotTimestamp(
                simulation_seconds=12.5,
                frame=321,
                host_capture_started_monotonic_ns=1_000,
                host_capture_completed_monotonic_ns=1_250,
            ),
        ),
        state=VehicleState(
            timestamp=12.5,
            speed_kmh=80.0,
            steering=-0.1,
            throttle=0.4,
            brake=0.2,
            lane_id=2,
            indicator="left",
        ),
    )


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as csv_file:
        return list(csv.reader(csv_file))


def test_record_forwards_explicit_timestamp_without_observation() -> None:
    observation = _observation()
    source = FakeObservationSource(observation)
    writer = RecordingEventWriter()
    segment = _segment()
    event = ResearchEvent(event_type="segment_started")
    recorder = ResearchEventRecorder(source, writer, segment)

    recorder.record(observation.timestamp, event)

    assert source.call_count == 0
    assert len(writer.calls) == 1
    written_segment, written_timestamp, written_event = writer.calls[0]
    assert written_segment is segment
    assert written_timestamp is observation.timestamp
    assert written_event is event
    assert writer.closed is False


def test_record_now_uses_one_observation_and_returns_its_timestamp() -> None:
    observation = _observation()
    source = FakeObservationSource(observation)
    writer = RecordingEventWriter()
    segment = _segment()
    event = ResearchEvent(event_type="forced_disengagement", event_value="brake")
    recorder = ResearchEventRecorder(source, writer, segment)

    timestamp = recorder.record_now(event)

    assert source.call_count == 1
    assert timestamp is observation.timestamp
    assert len(writer.calls) == 1
    written_segment, written_timestamp, written_event = writer.calls[0]
    assert written_segment is segment
    assert written_timestamp is observation.timestamp
    assert written_event is event
    assert writer.closed is False


def test_record_now_writes_real_event_csv_and_leaves_logger_open(tmp_path: Path) -> None:
    observation = _observation()
    source = FakeObservationSource(observation)
    segment = _segment()
    logger = ResearchCsvLogger(_study_run(), tmp_path)
    recorder = ResearchEventRecorder(source, logger, segment)

    recorder.record_now(
        ResearchEvent(event_type="recommendation_confirmed", event_value="left")
    )
    logger.write_telemetry(segment, observation.timestamp, observation.state)
    logger.close()

    row = dict(zip(RESEARCH_EVENT_HEADER, _read_rows(logger.event_path)[1]))
    assert row["study_run_id"] == "run-001"
    assert row["participant_id"] == "participant-007"
    assert row["segment_id"] == "segment-m1"
    assert row["phase"] == "MODULE_1"
    assert row["condition"] == "SURT"
    assert row["host_monotonic_ns"] == "1250"
    assert row["host_utc_ns"] == "1700000000"
    assert row["carla_frame"] == "321"
    assert row["carla_simulation_seconds"] == "12.5"
    assert row["event_type"] == "recommendation_confirmed"
    assert row["event_value"] == "left"
