from __future__ import annotations

import csv
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
from src.experiment.telemetry import (
    ResearchTelemetryConfig,
    ResearchTelemetryConfigError,
    run_research_telemetry,
)
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.logging.csv_logger import (
    RESEARCH_TELEMETRY_HEADER,
    ResearchCsvLogger,
    ResearchEvent,
)
from src.vehicle import VehicleObservation, VehicleState


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


class FakeObservationClient:
    def __init__(self, observation: VehicleObservation) -> None:
        self.observation = observation
        self.call_count = 0

    def get_observation(self) -> VehicleObservation:
        self.call_count += 1
        return self.observation


class RecordingTelemetryWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[SegmentContext, TimestampEnvelope, VehicleState]] = []
        self.closed = False

    def write_telemetry(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        state: VehicleState,
    ) -> None:
        self.calls.append((segment, timestamp, state))

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
    timestamp = TimestampEnvelope(
        host=HostClockTimestamp(monotonic_ns=1_250, utc_ns=1_700_000_000),
        carla_snapshot=CarlaSnapshotTimestamp(
            simulation_seconds=12.5,
            frame=321,
            host_capture_started_monotonic_ns=1_000,
            host_capture_completed_monotonic_ns=1_250,
        ),
    )
    return VehicleObservation(
        timestamp=timestamp,
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


def test_run_research_telemetry_writes_each_observation_without_closing(monkeypatch):
    from src.experiment import telemetry

    clock = FakeClock()
    observation = _observation()
    client = FakeObservationClient(observation)
    writer = RecordingTelemetryWriter()
    segment = _segment()
    monkeypatch.setattr(telemetry.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(telemetry.time, "sleep", clock.sleep)

    result = run_research_telemetry(
        client,
        writer,
        ResearchTelemetryConfig(segment, duration=0.25, sample_interval=0.1),
    )

    assert result.sample_count == 3
    assert client.call_count == 3
    assert len(writer.calls) == 3
    for written_segment, written_timestamp, written_state in writer.calls:
        assert written_segment is segment
        assert written_timestamp is observation.timestamp
        assert written_state is observation.state
    assert writer.closed is False


@pytest.mark.parametrize(
    ("duration", "sample_interval"),
    [(0.0, 0.1), (-1.0, 0.1), (1.0, 0.0), (1.0, -0.1)],
)
def test_research_telemetry_config_rejects_non_positive_timing(
    duration: float,
    sample_interval: float,
) -> None:
    with pytest.raises(ResearchTelemetryConfigError):
        ResearchTelemetryConfig(_segment(), duration, sample_interval)


def test_run_research_telemetry_writes_real_csv_and_leaves_logger_open(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.experiment import telemetry

    clock = FakeClock()
    observation = _observation()
    segment = _segment()
    logger = ResearchCsvLogger(_study_run(), tmp_path)
    monkeypatch.setattr(telemetry.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(telemetry.time, "sleep", clock.sleep)

    result = run_research_telemetry(
        FakeObservationClient(observation),
        logger,
        ResearchTelemetryConfig(segment, duration=0.05, sample_interval=0.1),
    )
    logger.write_event(
        segment,
        observation.timestamp,
        ResearchEvent(event_type="telemetry_completed"),
    )
    logger.close()

    row = dict(zip(RESEARCH_TELEMETRY_HEADER, _read_rows(logger.telemetry_path)[1]))
    assert result.sample_count == 1
    assert row["study_run_id"] == "run-001"
    assert row["segment_id"] == "segment-m1"
    assert row["host_monotonic_ns"] == "1250"
    assert row["host_utc_ns"] == "1700000000"
    assert row["carla_frame"] == "321"
    assert row["carla_simulation_seconds"] == "12.5"
    assert row["speed_kmh"] == "80.0"
    assert row["steering"] == "-0.1"
    assert row["throttle"] == "0.4"
    assert row["brake"] == "0.2"
