from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class P8Error(RuntimeError):
    pass


class Mode(StrEnum):
    DRY = "dry"
    ACCELERATED = "accelerated"
    DEV_LIVE_DRIVE = "dev-live-drive"
    DEV_REHEARSAL = "dev-rehearsal"
    PARTICIPANT = "participant"


class Stage(StrEnum):
    SESSION_CREATED = "SESSION_CREATED"
    CONSENT_AND_ELIGIBILITY = "CONSENT_AND_ELIGIBILITY"
    TRAINING = "TRAINING"
    EYE_TRACKER_CALIBRATION = "EYE_TRACKER_CALIBRATION"
    MODULE_1_PREPARE = "MODULE_1_PREPARE"
    MODULE_1_RUNNING = "MODULE_1_RUNNING"
    MODULE_1_COMPLETE = "MODULE_1_COMPLETE"
    REST = "REST"
    MODULE_2_PREPARE = "MODULE_2_PREPARE"
    MODULE_2_RUNNING = "MODULE_2_RUNNING"
    FINAL_HAZARD = "FINAL_HAZARD"
    POST_EXPERIMENT = "POST_EXPERIMENT"
    SESSION_COMPLETE = "SESSION_COMPLETE"
    ABORTED = "ABORTED"


SEQUENCE = tuple(stage for stage in Stage if stage is not Stage.ABORTED)
TRAINING_REQUIREMENTS = (
    "manual_driving",
    "noa_on_off",
    "brake_disengage_reactivate",
    "exit_confirm",
    "exit_reject",
    "surt",
    "operator_assessment",
)
RECEIPTS = {
    Stage.CONSENT_AND_ELIGIBILITY: ("consent", "eligibility"),
    Stage.TRAINING: TRAINING_REQUIREMENTS,
    Stage.EYE_TRACKER_CALIBRATION: ("calibration_boundary",),
    Stage.POST_EXPERIMENT: ("survey", "debrief", "data_use"),
}


@dataclass(frozen=True, slots=True)
class TimingProfile:
    label: str
    module_1_s: float
    rest_s: float
    module_2_s: float

    @classmethod
    def production(cls) -> TimingProfile:
        return cls("V5_NOMINAL", 600, 120, 780)

    @classmethod
    def development(cls, mode: Mode) -> TimingProfile:
        if mode is Mode.PARTICIPANT:
            raise P8Error("PARTICIPANT_TIMING_IS_NOT_DEVELOPMENT")
        if mode is Mode.ACCELERATED:
            return cls("DEV_ACCELERATED", 4, 2, 4)
        if mode is Mode.DEV_LIVE_DRIVE:
            return cls("DEV_LIVE_PARTIAL", 0, 0, 0)
        if mode is Mode.DEV_REHEARSAL:
            return cls.production()
        return cls("DEV_VIRTUAL_NOMINAL", 600, 120, 780)

    def minimum(self, stage: Stage) -> float:
        return {
            Stage.MODULE_1_RUNNING: self.module_1_s,
            Stage.REST: self.rest_s,
            Stage.MODULE_2_RUNNING: self.module_2_s,
        }.get(stage, 0)


class VirtualTimeline:
    def __init__(self, origin_ns: int, origin_utc_ns: int) -> None:
        self.origin_ns, self.origin_utc_ns = origin_ns, origin_utc_ns
        self.elapsed_ns = 0

    def advance(self, seconds: float) -> None:
        if not isfinite(seconds) or seconds < 0:
            raise P8Error("INVALID_VIRTUAL_TIME_STEP")
        self.elapsed_ns += round(seconds * 1e9)

    def monotonic_ns(self) -> int:
        return self.origin_ns + self.elapsed_ns
