from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Final, assert_never

from src.experiment.automation_interaction import AutomationInteractionConfig
from src.experiment.context import (
    ExperimentCondition,
    ExperimentModule,
    Module1Condition,
    Module2Condition,
)
from src.experiment.noa_runtime import NoAControlConfig
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig

DEFAULT_RESEARCH_VEHICLE_BLUEPRINT: Final = "vehicle.mercedes.coupe_2020"
RESEARCH_LIVE_SMOKE_MAX_DURATION_SECONDS: Final = 20.0
RESEARCH_DEVELOPMENT_VALIDATION_MAX_DURATION_SECONDS: Final = 120.0
RESEARCH_LIVE_SMOKE_MAX_THROTTLE: Final = 0.4
RESERVED_RESEARCH_NOA_KEYS: Final = frozenset("wsadpvhzx")


class ResearchNoARunMode(Enum):
    DRY_RUN = "dry-run"
    LIVE_SMOKE = "live-smoke"


@dataclass(frozen=True, slots=True)
class ResearchAutomationInteractionConfig:
    module: ExperimentModule
    condition: ExperimentCondition
    policy: AutomationInteractionConfig
    initial_lane_change_in_progress: bool = False
    initial_stage: str | None = None
    noa_key: str = "n"

    def __post_init__(self) -> None:
        match self.module:
            case ExperimentModule.MODULE_1:
                valid = self.condition in set(Module1Condition)
            case ExperimentModule.MODULE_2:
                valid = self.condition in set(Module2Condition)
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise ResearchNoAConfigError(
                "condition", f"is invalid for {self.module.value}"
            )
        if (
            len(self.noa_key) != 1
            or not self.noa_key.isascii()
            or not self.noa_key.isalpha()
            or self.noa_key != self.noa_key.lower()
            or self.noa_key.lower() in RESERVED_RESEARCH_NOA_KEYS
        ):
            raise ResearchNoAConfigError(
                "noa_key", "must be one non-conflicting ASCII letter"
            )


@dataclass(frozen=True, slots=True)
class ResearchNoAConfigError(ValueError):
    field: str
    requirement: str

    def __str__(self) -> str:
        return f"{self.field} {self.requirement}"


@dataclass(frozen=True, slots=True)
class ResearchNoARunConfig:
    duration: float
    control_config: NoAControlConfig
    mode: ResearchNoARunMode = ResearchNoARunMode.DRY_RUN
    spawn_index: int | None = None
    host: str = "127.0.0.1"
    port: int = 2000
    timeout: float = 5.0
    vehicle_blueprint: str = DEFAULT_RESEARCH_VEHICLE_BLUEPRINT
    front_camera_only: bool = False
    pi_trace: bool = False
    camera_diagnostics: bool = False
    run_id: str | None = None
    automation_interaction: ResearchAutomationInteractionConfig | None = None
    persistence: ResearchPersistenceConfig | None = None
    exit_assistance: ResearchExitAssistanceConfig | None = None
    development_validation: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.duration) or self.duration <= 0.0:
            raise ResearchNoAConfigError("duration", "must be finite and > 0")
        if self.development_validation and (
            self.mode is not ResearchNoARunMode.LIVE_SMOKE
            or self.exit_assistance is None
        ):
            raise ResearchNoAConfigError(
                "development_validation",
                "requires live smoke with P5 exit assistance; not an experiment protocol",
            )
        if self.spawn_index is not None and self.spawn_index < 0:
            raise ResearchNoAConfigError("spawn_index", "must be >= 0")
        if self.mode is ResearchNoARunMode.LIVE_SMOKE:
            if self.spawn_index is None:
                raise ResearchNoAConfigError(
                    "spawn_index",
                    "is required for live smoke",
                )
            self._validate_live_safety_caps()
        elif self.automation_interaction is not None:
            raise ResearchNoAConfigError(
                "automation_interaction", "requires live smoke mode"
            )
        if self.persistence is not None:
            if self.automation_interaction is None:
                raise ResearchNoAConfigError(
                    "persistence", "requires automation interaction"
                )
            if self.run_id != self.persistence.run_id:
                raise ResearchNoAConfigError("run_id", "must match persistence run_id")
            if (
                self.automation_interaction.module is not self.persistence.module
                or self.automation_interaction.condition
                is not self.persistence.condition
            ):
                raise ResearchNoAConfigError(
                    "automation_interaction",
                    "must match the assigned module condition",
                )
        if self.exit_assistance is not None:
            if self.automation_interaction is None or self.persistence is None:
                raise ResearchNoAConfigError(
                    "exit_assistance", "requires interaction and persistence"
                )
            if self.automation_interaction.noa_key in (
                self.exit_assistance.confirm_key,
                self.exit_assistance.reject_key,
            ):
                raise ResearchNoAConfigError(
                    "exit_assistance", "confirm key conflicts with NoA key"
                )

    def _validate_live_safety_caps(self) -> None:
        longitudinal = self.control_config.longitudinal
        lateral = self.control_config.lateral
        capped_values = (
            (
                "duration",
                self.duration,
                (
                    RESEARCH_DEVELOPMENT_VALIDATION_MAX_DURATION_SECONDS
                    if self.development_validation
                    else RESEARCH_LIVE_SMOKE_MAX_DURATION_SECONDS
                ),
            ),
            ("target_speed_kmh", longitudinal.target_speed_kmh, 20.0),
            (
                "max_throttle",
                longitudinal.max_throttle,
                RESEARCH_LIVE_SMOKE_MAX_THROTTLE,
            ),
            ("max_brake", longitudinal.max_brake, 0.5),
            ("max_steering", lateral.max_steering, 0.15),
        )
        for name, value, cap in capped_values:
            if value > cap:
                raise ResearchNoAConfigError(name, f"must be <= {cap}")


def parse_arguments(argv: Sequence[str] | None = None) -> ResearchNoARunConfig:
    from src.scenario.research_noa_cli import parse_arguments as parse_cli_arguments

    return parse_cli_arguments(argv)
