from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitContext
from src.experiment.noa_runtime import NoAControlConfig
from src.p7.types import Json
from src.p8.model import Stage
from src.p8.operator_exit_result import ExitOpportunityResult
from src.scenario.research_exit_config import (
    ExitEligibilityWindow,
    ResearchExitAssistanceConfig,
)
from src.scenario.research_noa_config import ResearchNoARunConfig
from src.scenario.research_noa_log_setup import OpenResearchLog
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_speed_policy import (
    ResearchSpeedPolicy,
    ResearchSpeedPolicyError,
)


class DevOutcome(StrEnum):
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"


class TrainingRun(StrEnum):
    MANUAL_NOA = "MANUAL_NOA"


@dataclass(frozen=True, slots=True)
class RegistrationRequest:
    participant_id: str
    run_id: str
    assignment_file: Path | None = None
    m1_condition: Module1Condition | None = None
    m2_condition: Module2Condition | None = None
    route_id: str | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    name: str
    status: str
    value: Json


type RunnerBuilder = Callable[
    [ResearchNoARunConfig, OpenResearchLog], ResearchNoARunner
]


@dataclass(frozen=True, slots=True)
class OperatorRuntimeConfig:
    spawn_index: int
    control_config: NoAControlConfig
    interaction_policy: AutomationInteractionConfig
    runner_builder: RunnerBuilder
    speed_policy: ResearchSpeedPolicy | None
    host: str = "127.0.0.1"
    port: int = 2000
    timeout: float = 5.0
    rear_mirror_enabled: bool = False
    driver_input_source: Callable[[], DriverInput] | None = None
    module_exit: ResearchExitAssistanceConfig | None = None
    training_exit: ResearchExitAssistanceConfig | None = None
    traffic_manager_port: int = 8000
    module_1_exit_window: ExitEligibilityWindow | None = None
    module_2_exit_window: ExitEligibilityWindow | None = None

    def __post_init__(self) -> None:
        self.validate_safety_contract()

    def validate_safety_contract(self) -> None:
        if not self.interaction_policy.steering_deactivation_enabled:
            raise ResearchSpeedPolicyError(
                "steering_deactivation_enabled",
                "must be enabled for the P8 operator runtime",
            )
        if self.speed_policy is not None:
            raise ResearchSpeedPolicyError(
                "speed_policy",
                "must be unset because P8 uses Traffic Manager default speed",
            )
        if self.control_config.longitudinal.target_speed_kmh is not None:
            raise ResearchSpeedPolicyError(
                "target_speed_kmh",
                "must be unset because P8 uses Traffic Manager default speed",
            )
        if not 1 <= self.traffic_manager_port <= 65535:
            raise ResearchSpeedPolicyError(
                "traffic_manager_port",
                "must be within [1, 65535]",
            )
        if (
            self.module_exit is not None
            and self.module_exit.context is not ExitContext.MODULE
        ):
            raise ResearchSpeedPolicyError(
                "module_exit",
                "requires MODULE context",
            )
        if (
            self.training_exit is not None
            and self.training_exit.context is not ExitContext.TRAINING
        ):
            raise ResearchSpeedPolicyError(
                "training_exit",
                "requires TRAINING context",
            )
        for field, exit_config in (
            ("module_exit", self.module_exit),
            ("training_exit", self.training_exit),
        ):
            if exit_config is None:
                continue
            if exit_config.assisted_execution_enabled:
                raise ResearchSpeedPolicyError(
                    field,
                    "must disable assisted execution",
                )
            if not exit_config.indicator_confirmation:
                raise ResearchSpeedPolicyError(
                    field,
                    "must enable indicator confirmation",
                )
        module_windows = (self.module_1_exit_window, self.module_2_exit_window)
        if self.module_exit is not None and any(
            window is None for window in module_windows
        ):
            raise ResearchSpeedPolicyError(
                "module_exit",
                "requires explicit Module 1 and Module 2 eligibility windows",
            )
        if self.module_exit is None and any(
            window is not None for window in module_windows
        ):
            raise ResearchSpeedPolicyError(
                "module_exit",
                "eligibility windows require a configured module route",
            )
        for field, window, duration in (
            ("module_1_exit_window", self.module_1_exit_window, 600.0),
            ("module_2_exit_window", self.module_2_exit_window, 780.0),
        ):
            if window is not None and window.close_s > duration:
                raise ResearchSpeedPolicyError(
                    field,
                    f"close must not exceed nominal duration {duration}",
                )


@dataclass(frozen=True, slots=True)
class ModuleRunResult:
    stage: Stage
    requested_duration_s: float
    actual_active_drive_s: float
    initial_mode: str
    automation_on_seconds: float
    activation_count: int
    deactivation_count: int
    reactivation_count: int
    first_activation_latency_s: float | None
    observed_on_proportion: float | None
    nominal_on_proportion: float | None
    user_exited: bool
    planned_duration_s: float = 0.0
    actual_active_start_session_s: float = 0.0
    actual_active_end_session_s: float = 0.0
    runtime_started_session_s: float = 0.0
    runtime_closed_session_s: float = 0.0
    completion_reason: str = "TIME_LIMIT_REACHED"
    training_completed_early: bool = False
    nominal_request: bool = False
    exit_opportunity: ExitOpportunityResult | None = None

    @property
    def window_complete(self) -> bool:
        return (
            self.stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING)
            and self.nominal_request
            and self.actual_active_drive_s >= self.planned_duration_s
            and not self.user_exited
            and self.completion_reason == "TIME_LIMIT_REACHED"
        )

    def manifest(self) -> dict[str, Json]:
        return {
            "stage": self.stage.value,
            "requested_duration_s": self.requested_duration_s,
            "actual_active_drive_s": self.actual_active_drive_s,
            "initial_mode": self.initial_mode,
            "automation_on_seconds": self.automation_on_seconds,
            "activation_count": self.activation_count,
            "deactivation_count": self.deactivation_count,
            "reactivation_count": self.reactivation_count,
            "first_activation_latency_s": self.first_activation_latency_s,
            "observed_on_proportion": self.observed_on_proportion,
            "nominal_on_proportion": self.nominal_on_proportion,
            "user_exited": self.user_exited,
            "planned_duration_s": self.planned_duration_s,
            "actual_active_start_session_s": self.actual_active_start_session_s,
            "actual_active_end_session_s": self.actual_active_end_session_s,
            "runtime_started_session_s": self.runtime_started_session_s,
            "runtime_closed_session_s": self.runtime_closed_session_s,
            "completion_reason": self.completion_reason,
            "training_completed_early": self.training_completed_early,
            "nominal_request": self.nominal_request,
            "window_complete": self.window_complete,
            "exit_opportunity": (
                None
                if self.exit_opportunity is None
                else self.exit_opportunity.manifest()
            ),
        }


@dataclass(frozen=True, slots=True)
class OperatorSnapshot:
    stage: Stage
    participant_id: str
    run_id: str
    stage_elapsed_s: float
    closed: bool
    allowed_receipts: tuple[str, ...]
    receipts: dict[str, str]
    stage_approval: dict[str, Json] | None
    module_runs: tuple[ModuleRunResult, ...]
    analysis_eligible: bool
    deletion_status: str


@dataclass(frozen=True, slots=True)
class DoneClaim:
    completion_status: str
    stage: Stage
    participant_ready: bool
    cleanup_completed: bool
    manifest_path: Path
