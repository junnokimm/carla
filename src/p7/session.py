from __future__ import annotations

import json
from dataclasses import asdict
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
from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchCsvLogger
from src.p7.config import P7Config
from src.p7.events import HazardEventLogger
from src.p7.types import EventKind, Json, P7Event


class DevelopmentSession:
    def __init__(self, directory: Path, *, config: P7Config, module: ExperimentModule,
                 participant_id: str, origin: HostClockTimestamp, dry: bool) -> None:
        directory.mkdir(parents=True, exist_ok=False)
        self.directory, self.config, self.module, self.origin = directory, config, module, origin
        self.context = StudyRunContext(StudyRunId(directory.name), ParticipantId(participant_id),
                                       Module1Condition.NO_SURT, Module2Condition.MANUAL,
                                       route_id="DEV_LOCAL_STRAIGHT_CORRIDOR", scenario_version="P7-DEV-1",
                                       program_version="P7_FOUNDATION_UNVALIDATED")
        self.segment = SegmentContext(
            SegmentId(f"{directory.name}:p7"), self.context.study_run_id,
            ExperimentPhase.FINAL_HAZARD_ASSESSMENT if module is ExperimentModule.MODULE_2 else ExperimentPhase.MODULE_1,
            module, self.context.module_2_condition if module is ExperimentModule.MODULE_2 else self.context.module_1_condition,
            OutcomeFamily.HAZARD_RESPONSE if module is ExperimentModule.MODULE_2 else OutcomeFamily.AUTOMATION_CHOICE,
            route="development/p7-not-participant",
        )
        self.clock = SessionClock(origin)
        self.logger = ResearchCsvLogger(self.context, directory, session_clock=self.clock)
        self.events = HazardEventLogger(self.logger, self.segment)
        self.dry = dry
        self.last_timestamp = TimestampEnvelope(origin)
        self.emit(EventKind.CONFIG, self.last_timestamp, {"config": asdict(config),
                  "final_hazard_phase_shortcut": "DEV_ONLY_NOT_A_13_MINUTE_MODULE_RUN",
                  "ego_control_owner": "EXTERNAL_UNCHANGED", "safety_rescue_owner_binding": "BLOCKED_P8_REQUIRED"})

    def capture(self, event: P7Event) -> None:
        self.emit(event.kind, event.timestamp, event.payload)

    def emit(self, kind: EventKind, timestamp: TimestampEnvelope, payload: dict[str, Json]) -> None:
        self.last_timestamp = timestamp
        self.events.emit(kind, timestamp, {"evidence_mode": "DRY_FAKE_INPUTS" if self.dry else "DEV_STATIC_OBSERVER",
                         "hazard_validated": False, "participant_run": False, **payload})

    def close(self, summary: dict[str, Json]) -> None:
        try:
            with (self.directory / "summary.json").open("x", encoding="utf-8") as output:
                json.dump({"run_id": self.context.study_run_id, "participant_id": self.context.participant_id,
                           "module": self.module.value, "parameter_status": "DEV_ONLY",
                           "evidence_mode": "DRY_FAKE_INPUTS" if self.dry else "DEV_STATIC_OBSERVER",
                           "live_validation_status": "NOT_APPROVED", "hazard_validated": False,
                           "participant_run": False, **summary}, output, indent=2, allow_nan=False)
        finally:
            self.logger.close()
