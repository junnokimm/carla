from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchCsvLogger, ResearchEvent
from src.scenario.research_noa_persistence import (
    ResearchLogPaths,
    assignment_event_value,
    build_segment,
    build_study_run,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig


@dataclass(frozen=True, slots=True)
class OpenResearchLog:
    logger: ResearchCsvLogger
    paths: ResearchLogPaths


def open_research_log(
    config: ResearchPersistenceConfig,
    monotonic_ns: Callable[[], int],
    utc_ns: Callable[[], int],
) -> OpenResearchLog:
    baseline = HostClockTimestamp(monotonic_ns(), utc_ns())
    logger = ResearchCsvLogger(
        build_study_run(config),
        config.output_dir,
        session_clock=SessionClock(baseline),
    )
    logger.write_event(
        build_segment(config),
        TimestampEnvelope(host=baseline),
        ResearchEvent("assignment", assignment_event_value(config)),
    )
    return OpenResearchLog(
        logger,
        ResearchLogPaths(logger.telemetry_path, logger.event_path),
    )
