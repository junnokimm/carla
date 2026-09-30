from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.experiment.context import SegmentContext
from src.experiment.timestamp import TimestampEnvelope
from src.logging.csv_logger import ResearchEvent
from src.vehicle import VehicleObservation


class EventObservationSource(Protocol):
    def get_observation(self) -> VehicleObservation: ...


class ResearchEventWriter(Protocol):
    def write_event(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        event: ResearchEvent,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ResearchEventRecorder:
    observation_source: EventObservationSource
    writer: ResearchEventWriter
    segment: SegmentContext

    def record(self, timestamp: TimestampEnvelope, event: ResearchEvent) -> None:
        self.writer.write_event(self.segment, timestamp, event)

    def record_now(self, event: ResearchEvent) -> TimestampEnvelope:
        timestamp = self.observation_source.get_observation().timestamp
        self.record(timestamp, event)
        return timestamp
