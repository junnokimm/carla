from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from src.experiment.context import SegmentContext
from src.experiment.timestamp import TimestampEnvelope
from src.vehicle import VehicleObservation, VehicleState


class ResearchTelemetryConfigError(ValueError):
    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class ResearchTelemetryConfig:
    segment: SegmentContext
    duration: float
    sample_interval: float

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ResearchTelemetryConfigError("duration")
        if self.sample_interval <= 0:
            raise ResearchTelemetryConfigError("sample_interval")


@dataclass(frozen=True, slots=True)
class ResearchTelemetryResult:
    sample_count: int


class VehicleObservationSource(Protocol):
    def get_observation(self) -> VehicleObservation: ...


class ResearchTelemetryWriter(Protocol):
    def write_telemetry(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        state: VehicleState,
    ) -> None: ...


def run_research_telemetry(
    vehicle_client: VehicleObservationSource,
    writer: ResearchTelemetryWriter,
    config: ResearchTelemetryConfig,
) -> ResearchTelemetryResult:
    started_at = time.monotonic()
    sample_count = 0
    while time.monotonic() - started_at < config.duration:
        observation = vehicle_client.get_observation()
        writer.write_telemetry(
            config.segment,
            observation.timestamp,
            observation.state,
        )
        sample_count += 1
        time.sleep(config.sample_interval)
    return ResearchTelemetryResult(sample_count=sample_count)
