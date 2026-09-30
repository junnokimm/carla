from __future__ import annotations

import csv
from collections.abc import Sequence
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
from src.experiment.telemetry import ResearchTelemetryConfig, run_research_telemetry
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.logging.csv_logger import (
    RESEARCH_EVENT_HEADER,
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


class SequenceObservationSource:
    def __init__(self, observations: Sequence[VehicleObservation]) -> None:
        self._observations = iter(observations)
        self.call_count = 0

    def get_observation(self) -> VehicleObservation:
        self.call_count += 1
        return next(self._observations)


def _observation(
    host_monotonic_ns: int,
    host_utc_ns: int,
    frame: int,
    simulation_seconds: float,
) -> VehicleObservation:
    return VehicleObservation(
        timestamp=TimestampEnvelope(
            host=HostClockTimestamp(
                monotonic_ns=host_monotonic_ns,
                utc_ns=host_utc_ns,
            ),
            carla_snapshot=CarlaSnapshotTimestamp(
                simulation_seconds=simulation_seconds,
                frame=frame,
                host_capture_started_monotonic_ns=host_monotonic_ns - 10,
                host_capture_completed_monotonic_ns=host_monotonic_ns,
            ),
        ),
        state=VehicleState(
            timestamp=simulation_seconds,
            speed_kmh=float(frame),
            steering=0.1,
            throttle=0.4,
            brake=0.0,
            lane_id=2,
            indicator="off",
        ),
    )


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as csv_file:
        return list(csv.reader(csv_file))


def test_research_runtime_reconstructs_interleaved_timeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.experiment import telemetry

    study_run = StudyRunContext(
        study_run_id=StudyRunId("run-001"),
        participant_id=ParticipantId("participant-007"),
        module_1_condition=Module1Condition.SURT,
        module_2_condition=Module2Condition.MANUAL,
    )
    segment = SegmentContext(
        segment_id=SegmentId("segment-m1"),
        study_run_id=study_run.study_run_id,
        phase=ExperimentPhase.MODULE_1,
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.SURT,
        outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
    )
    observations = (
        _observation(1_000, 1_700_000_001_000, 100, 10.0),
        _observation(2_000, 1_700_000_002_000, 101, 10.1),
        _observation(3_000, 1_700_000_003_000, 102, 10.2),
    )
    source = SequenceObservationSource(observations)
    clock = FakeClock()
    logger = ResearchCsvLogger(study_run, tmp_path)
    config = ResearchTelemetryConfig(segment, duration=0.05, sample_interval=0.1)
    recorder = ResearchEventRecorder(source, logger, segment)
    monkeypatch.setattr(telemetry.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(telemetry.time, "sleep", clock.sleep)

    first_result = run_research_telemetry(source, logger, config)
    event_timestamp = recorder.record_now(
        ResearchEvent(event_type="automation_activated", event_value="driver_request")
    )
    second_result = run_research_telemetry(source, logger, config)
    logger.close()

    telemetry_rows = _read_rows(logger.telemetry_path)
    event_rows = _read_rows(logger.event_path)
    telemetry_data = [
        dict(zip(RESEARCH_TELEMETRY_HEADER, row)) for row in telemetry_rows[1:]
    ]
    event_data = [dict(zip(RESEARCH_EVENT_HEADER, row)) for row in event_rows[1:]]

    assert first_result.sample_count == 1
    assert second_result.sample_count == 1
    assert source.call_count == 3
    assert event_timestamp is observations[1].timestamp
    assert len(telemetry_rows) == 3
    assert len(event_rows) == 2
    assert all(len(row) == len(RESEARCH_TELEMETRY_HEADER) for row in telemetry_rows)
    assert all(len(row) == len(RESEARCH_EVENT_HEADER) for row in event_rows)

    expected_context = {
        "study_run_id": "run-001",
        "participant_id": "participant-007",
        "segment_id": "segment-m1",
        "phase": "MODULE_1",
        "module": "MODULE_1",
        "condition": "SURT",
        "outcome_family": "AUTOMATION_CHOICE",
    }
    for row in (*telemetry_data, *event_data):
        assert {key: row[key] for key in expected_context} == expected_context

    assert [
        (
            row["host_monotonic_ns"],
            row["host_utc_ns"],
            row["carla_frame"],
            row["carla_simulation_seconds"],
        )
        for row in telemetry_data
    ] == [
        ("1000", "1700000001000", "100", "10.0"),
        ("3000", "1700000003000", "102", "10.2"),
    ]
    assert (
        event_data[0]["host_monotonic_ns"],
        event_data[0]["host_utc_ns"],
        event_data[0]["carla_frame"],
        event_data[0]["carla_simulation_seconds"],
        event_data[0]["event_type"],
        event_data[0]["event_value"],
    ) == (
        "2000",
        "1700000002000",
        "101",
        "10.1",
        "automation_activated",
        "driver_request",
    )

    timeline = [("telemetry", row) for row in telemetry_data]
    timeline.extend(("event", row) for row in event_data)
    timeline.sort(key=lambda item: int(item[1]["host_monotonic_ns"]))
    assert [
        (
            kind,
            int(row["host_monotonic_ns"]),
            int(row["carla_frame"]),
            float(row["carla_simulation_seconds"]),
        )
        for kind, row in timeline
    ] == [
        ("telemetry", 1_000, 100, 10.0),
        ("event", 2_000, 101, 10.1),
        ("telemetry", 3_000, 102, 10.2),
    ]
