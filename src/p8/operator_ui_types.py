from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.p8.model import P8Error
from src.p8.operator_model import (
    DevOutcome,
    DoneClaim,
    ModuleRunResult,
    OperatorSnapshot,
    Receipt,
    RegistrationRequest,
    TrainingRun,
)


class OperatorControl(Protocol):
    def register_id(self, request: RegistrationRequest) -> OperatorSnapshot: ...
    def snapshot(self) -> OperatorSnapshot: ...
    def record_actual(self, receipt: Receipt) -> OperatorSnapshot: ...
    def advance(self) -> OperatorSnapshot | DoneClaim: ...
    def execute_training(
        self, training: TrainingRun, duration: float
    ) -> ModuleRunResult: ...
    def execute_current_module(
        self, duration: float | None = None
    ) -> ModuleRunResult: ...
    def approve_dev_outcome(
        self, outcome: DevOutcome, reason: str
    ) -> OperatorSnapshot: ...
    def record_execution_policy_outcome(
        self, outcome: DevOutcome, reason: str
    ) -> OperatorSnapshot: ...
    def abort(self, reason: str) -> DoneClaim: ...


@dataclass(frozen=True, slots=True)
class OperatorUiConfig:
    registration: RegistrationRequest
    training_duration_s: float
    drive_duration_s: float | None
    window_size: tuple[int, int] = (1280, 720)

    def __post_init__(self) -> None:
        if not 1.0 <= self.training_duration_s <= 600.0:
            raise P8Error("OPERATOR_UI_TRAINING_DURATION_REQUIRES_1_TO_600_SECONDS")
        if (
            self.drive_duration_s is not None
            and not 1.0 <= self.drive_duration_s <= 60.0
        ):
            raise P8Error("OPERATOR_UI_DRIVE_DURATION_REQUIRES_1_TO_60_SECONDS")
