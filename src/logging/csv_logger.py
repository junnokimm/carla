from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO, assert_never

from src.experiment.context import ExperimentPhase, SegmentContext, StudyRunContext
from src.experiment.timestamp import TimestampEnvelope
from src.vehicle import VehicleState

CSV_HEADER: Final = (
    "timestamp",
    "speed_kmh",
    "steering",
    "throttle",
    "brake",
    "lane_id",
    "indicator",
)

RESEARCH_COMMON_HEADER: Final = (
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

RESEARCH_TELEMETRY_HEADER: Final = RESEARCH_COMMON_HEADER + (
    "vehicle_state_timestamp_legacy",
    "speed_kmh",
    "steering",
    "throttle",
    "brake",
    "lane_id",
    "indicator",
)

RESEARCH_EVENT_HEADER: Final = RESEARCH_COMMON_HEADER + (
    "event_type",
    "event_value",
)

type CsvValue = str | int | float


class ResearchCsvError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResearchEvent:
    event_type: str
    event_value: str | None = None

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ResearchCsvError("event_type must not be blank")


class VehicleStateCsvLogger:
    """Write VehicleState telemetry to one CSV session file."""

    def __init__(self, session_id: str, data_dir: str | Path = "data") -> None:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"vehicle_state_{session_id}.csv"
        self._file: TextIO = self.path.open("w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_HEADER)

    def write(self, state: VehicleState) -> None:
        self._writer.writerow(
            (
                state.timestamp,
                state.speed_kmh,
                state.steering,
                state.throttle,
                state.brake,
                "" if state.lane_id is None else state.lane_id,
                state.indicator,
            )
        )

    def close(self) -> None:
        self._file.close()


class ResearchCsvLogger:
    def __init__(
        self,
        study_run: StudyRunContext,
        data_dir: str | Path = "data",
    ) -> None:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self._study_run = study_run
        self.telemetry_path = (
            directory / f"research_telemetry_{study_run.study_run_id}.csv"
        )
        self.event_path = directory / f"research_events_{study_run.study_run_id}.csv"
        try:
            self._telemetry_file: TextIO = self.telemetry_path.open("x", newline="")
        except FileExistsError:
            raise ResearchCsvError(
                f"research CSV already exists for study run {study_run.study_run_id}"
            ) from None
        try:
            self._event_file: TextIO = self.event_path.open("x", newline="")
        except FileExistsError:
            self._telemetry_file.close()
            self.telemetry_path.unlink()
            raise ResearchCsvError(
                f"research CSV already exists for study run {study_run.study_run_id}"
            ) from None
        self._telemetry_writer = csv.writer(self._telemetry_file)
        self._event_writer = csv.writer(self._event_file)
        self._telemetry_writer.writerow(RESEARCH_TELEMETRY_HEADER)
        self._event_writer.writerow(RESEARCH_EVENT_HEADER)

    def write_telemetry(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        state: VehicleState,
    ) -> None:
        self._telemetry_writer.writerow(
            self._common_values(segment, timestamp)
            + (
                state.timestamp,
                state.speed_kmh,
                state.steering,
                state.throttle,
                state.brake,
                "" if state.lane_id is None else state.lane_id,
                state.indicator,
            )
        )

    def write_event(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        event: ResearchEvent,
    ) -> None:
        self._event_writer.writerow(
            self._common_values(segment, timestamp)
            + (
                event.event_type,
                "" if event.event_value is None else event.event_value,
            )
        )

    def close(self) -> None:
        self._telemetry_file.close()
        self._event_file.close()

    def _common_values(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
    ) -> tuple[CsvValue, ...]:
        if segment.study_run_id != self._study_run.study_run_id:
            raise ResearchCsvError(
                "segment and CSV logger must belong to the same study run"
            )

        match segment.phase:
            case ExperimentPhase.MODULE_1:
                if segment.condition is not self._study_run.module_1_condition:
                    raise ResearchCsvError(
                        "segment condition must match the assigned condition"
                    )
            case (
                ExperimentPhase.MODULE_2
                | ExperimentPhase.FINAL_HAZARD_ASSESSMENT
            ):
                if segment.condition is not self._study_run.module_2_condition:
                    raise ResearchCsvError(
                        "segment condition must match the assigned condition"
                    )
            case (
                ExperimentPhase.CONSENT_AND_ELIGIBILITY
                | ExperimentPhase.TRAINING
                | ExperimentPhase.EYE_TRACKER_CALIBRATION
                | ExperimentPhase.REST
                | ExperimentPhase.POST_EXPERIMENT
            ):
                pass
            case unreachable:
                assert_never(unreachable)

        carla = timestamp.carla_snapshot
        external_timestamps = (
            json.dumps(
                [
                    {
                        "source": external.source,
                        "raw_value": external.raw_value,
                        "unit": external.unit,
                    }
                    for external in timestamp.external_clocks
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if timestamp.external_clocks
            else ""
        )
        return (
            self._study_run.study_run_id,
            self._study_run.participant_id,
            self._study_run.module_1_condition.value,
            self._study_run.module_2_condition.value,
            segment.segment_id,
            segment.phase.value,
            "" if segment.module is None else segment.module.value,
            "" if segment.block is None else segment.block,
            "" if segment.condition is None else segment.condition.value,
            "" if segment.order is None else segment.order,
            "" if segment.route is None else segment.route,
            "" if segment.outcome_family is None else segment.outcome_family.value,
            timestamp.host.monotonic_ns,
            timestamp.host.utc_ns,
            "" if carla is None else carla.simulation_seconds,
            "" if carla is None else carla.frame,
            "" if carla is None else carla.host_capture_started_monotonic_ns,
            "" if carla is None else carla.host_capture_completed_monotonic_ns,
            external_timestamps,
        )
