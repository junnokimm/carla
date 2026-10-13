import json

from src.experiment.context import SegmentContext
from src.experiment.timestamp import TimestampEnvelope
from src.logging.csv_logger import ResearchCsvLogger
from src.logging.research_event import ResearchEvent
from src.p7.types import EventKind, Json, P7Event


class HazardEventLogger:
    def __init__(self, logger: ResearchCsvLogger, segment: SegmentContext) -> None:
        self.logger, self.segment = logger, segment

    def capture(self, event: P7Event) -> None:
        self.logger.write_event(self.segment, event.timestamp,
                                ResearchEvent(event.kind.value, json.dumps(event.payload, allow_nan=False, separators=(",", ":"))))

    def emit(self, kind: EventKind, timestamp: TimestampEnvelope, payload: dict[str, Json]) -> None:
        self.capture(P7Event(kind, timestamp, payload))
