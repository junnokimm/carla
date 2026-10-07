from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from src.experiment.assignment import ParticipantAssignment
from src.experiment.context import (
    ExperimentCondition,
    ExperimentModule,
    StudyRunId,
)


class ResearchPersistenceConfigError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResearchPersistenceConfig:
    assignment: ParticipantAssignment
    run_id: StudyRunId
    output_dir: Path
    module: ExperimentModule
    stage_label: str

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ResearchPersistenceConfigError("run_id must not be blank")
        if not self.stage_label.strip():
            raise ResearchPersistenceConfigError("stage_label must not be blank")

    @property
    def condition(self) -> ExperimentCondition:
        match self.module:
            case ExperimentModule.MODULE_1:
                return self.assignment.m1_condition
            case ExperimentModule.MODULE_2:
                return self.assignment.m2_condition
            case unreachable:
                assert_never(unreachable)
