from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from src.experiment.assignment import ParticipantAssignment
from src.experiment.context import (
    ExperimentModule,
    ExperimentPhase,
    OutcomeFamily,
    SegmentContext,
    SegmentId,
    StudyRunContext,
    StudyRunId,
)
from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.csv_logger import ResearchCsvLogger
from src.logging.research_event import ResearchEvent
from src.p7.types import Json
from src.p8.model import Mode, P8Error, Stage, TimingProfile, VirtualTimeline

if TYPE_CHECKING:
    from src.scenario.research_noa_log_setup import OpenResearchLog


class SessionServices:
    def __init__(
        self,
        root: Path,
        assignment: ParticipantAssignment,
        run_id: str,
        mode: Mode,
        *,
        provenance: str = "DEV_STUB",
        monotonic_ns: Callable[[], int] | None = None,
        utc_ns: Callable[[], int] | None = None,
    ) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", run_id) is None:
            raise P8Error("UNSAFE_RUN_ID")
        self.directory = root / run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.context = StudyRunContext(
            StudyRunId(run_id),
            assignment.participant_id,
            assignment.m1_condition,
            assignment.m2_condition,
            assignment.route_id,
            assignment.scenario_version,
            assignment.aoi_file_version,
            assignment.program_version,
        )
        self.assignment = assignment
        self._monotonic_ns = time.monotonic_ns if monotonic_ns is None else monotonic_ns
        self._utc_ns = time.time_ns if utc_ns is None else utc_ns
        origin = HostClockTimestamp(self._monotonic_ns(), self._utc_ns())
        self.clock = SessionClock(origin)
        self.timeline = VirtualTimeline(origin.monotonic_ns, origin.utc_ns)
        self.profile = (
            TimingProfile.production()
            if mode is Mode.PARTICIPANT
            else TimingProfile.development(mode)
        )
        self.mode, self.stage = mode, Stage.SESSION_CREATED
        self.provenance = provenance
        self.logger = ResearchCsvLogger(
            self.context, self.directory, session_clock=self.clock
        )
        self.closed = False

    def stamp(self) -> TimestampEnvelope:
        if self.mode in (Mode.DEV_LIVE_DRIVE, Mode.DEV_REHEARSAL, Mode.PARTICIPANT):
            return TimestampEnvelope(
                HostClockTimestamp(self._monotonic_ns(), self._utc_ns())
            )
        return TimestampEnvelope(
            HostClockTimestamp(
                self.timeline.monotonic_ns(),
                self.timeline.origin_utc_ns + self.timeline.elapsed_ns,
            )
        )

    @property
    def elapsed(self) -> float:
        return self.clock.elapsed_seconds(self.stamp().host)

    def segment(self) -> SegmentContext:
        stage = self.stage
        module, condition, outcome = None, None, None
        if stage in (
            Stage.MODULE_1_PREPARE,
            Stage.MODULE_1_RUNNING,
            Stage.MODULE_1_COMPLETE,
        ):
            phase, module = ExperimentPhase.MODULE_1, ExperimentModule.MODULE_1
            condition, outcome = (
                self.context.module_1_condition,
                OutcomeFamily.AUTOMATION_CHOICE,
            )
        elif stage in (
            Stage.MODULE_2_PREPARE,
            Stage.MODULE_2_RUNNING,
            Stage.FINAL_HAZARD,
        ):
            module, condition = (
                ExperimentModule.MODULE_2,
                self.context.module_2_condition,
            )
            phase = (
                ExperimentPhase.FINAL_HAZARD_ASSESSMENT
                if stage is Stage.FINAL_HAZARD
                else ExperimentPhase.MODULE_2
            )
            outcome = (
                OutcomeFamily.HAZARD_RESPONSE
                if stage is Stage.FINAL_HAZARD
                else OutcomeFamily.ATTENTION_ALLOCATION
            )
        else:
            phase = {
                Stage.SESSION_CREATED: ExperimentPhase.CONSENT_AND_ELIGIBILITY,
                Stage.CONSENT_AND_ELIGIBILITY: ExperimentPhase.CONSENT_AND_ELIGIBILITY,
                Stage.TRAINING: ExperimentPhase.TRAINING,
                Stage.EYE_TRACKER_CALIBRATION: ExperimentPhase.EYE_TRACKER_CALIBRATION,
                Stage.REST: ExperimentPhase.REST,
            }.get(stage, ExperimentPhase.POST_EXPERIMENT)
        return SegmentContext(
            SegmentId(f"{self.context.study_run_id}:{stage.value}"),
            self.context.study_run_id,
            phase,
            module,
            condition,
            outcome,
            route=self.context.route_id,
        )

    def emit(
        self,
        name: str,
        payload: dict[str, Json] | None = None,
        *,
        timestamp: TimestampEnvelope | None = None,
    ) -> None:
        if self.closed:
            raise P8Error("SESSION_LOG_CLOSED")
        body = {
            **(payload or {}),
            "orchestration_stage": self.stage.value,
            "participant_run": False,
            "development_mode": self.mode is not Mode.PARTICIPANT,
            "provenance": self.provenance,
            "hardware_validation": "NOT_HARDWARE_VALIDATED",
            "timing_profile": self.profile.label,
        }
        self.logger.write_event(
            self.segment(),
            timestamp or self.stamp(),
            ResearchEvent(name, json.dumps(body, allow_nan=False)),
        )

    def envelope(self) -> dict[str, Json]:
        return {
            "participant_id": self.context.participant_id,
            "run_id": self.context.study_run_id,
            "session_elapsed_s": self.elapsed,
            "stage": self.stage.value,
            "provenance": self.provenance,
            "hardware_validation": "NOT_HARDWARE_VALIDATED",
        }

    def save_manifest(self, payload: dict[str, Json]) -> None:
        target = self.directory / "session_manifest.json"
        temporary = self.directory / ".session_manifest.tmp"
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)

    def save_manifest_once(self, payload: dict[str, Json]) -> None:
        target = self.directory / "session_manifest.json"
        temporary = self.directory / ".session_manifest.tmp"
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise P8Error("SESSION_MANIFEST_EXISTS") from error
        finally:
            temporary.unlink(missing_ok=True)

    def manifest_base(self) -> dict[str, Json]:
        return {
            "schema_version": 1,
            "participant_id": self.context.participant_id,
            "run_id": self.context.study_run_id,
            "study_context": asdict(self.context),
            "origin": asdict(self.clock.origin),
            "participant_run": False,
            "development_mode": self.mode is not Mode.PARTICIPANT,
            "execution_mode": (
                "DEV_ACCELERATED_FULL_FLOW"
                if self.mode is Mode.ACCELERATED
                else self.mode.value
            ),
            "provenance": self.provenance,
            "timing_profile": self.profile.label,
            "production_nominal_durations": asdict(TimingProfile.production()),
            "participant_ready": False,
            "hardware_validated": False,
            "analysis_eligible": False,
        }

    def borrowed_research_log(self) -> OpenResearchLog:
        from src.scenario.research_noa_log_setup import OpenResearchLog
        from src.scenario.research_noa_persistence import ResearchLogPaths

        return OpenResearchLog(
            self.logger,
            ResearchLogPaths(self.logger.telemetry_path, self.logger.event_path),
            self.clock,
            self.context,
        )

    def close(self) -> None:
        if not self.closed:
            self.logger.close()
            self.closed = True
