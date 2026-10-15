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
    ExperimentPhase,
    Module1Condition,
    Module2Condition,
)
from src.experiment.exit_assistance import ExitContext
from src.experiment.noa_runtime import NoAControlConfig
from src.p6.config import P6Config
from src.p8.native_tm_diagnostics import NativeTrafficManagerDiagnosticsConfig
from src.p8.tsxw_policy import TsxwConfig
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_speed_policy import (
    EffectiveResearchSpeedPolicy,
    ResearchSpeedPolicy,
    ResearchSpeedPolicyError,
    resolve_speed_policy,
)

DEFAULT_RESEARCH_VEHICLE_BLUEPRINT: Final = "vehicle.mercedes.coupe_2020"
RESEARCH_LIVE_SMOKE_MAX_DURATION_SECONDS: Final = 60.0
RESEARCH_DEVELOPMENT_VALIDATION_MAX_DURATION_SECONDS: Final = 120.0
RESEARCH_OPERATOR_TRAINING_MAX_DURATION_SECONDS: Final = 600.0
RESEARCH_MODULE_1_NOMINAL_DURATION_SECONDS: Final = 600.0
RESEARCH_MODULE_2_NOMINAL_DURATION_SECONDS: Final = 780.0
RESEARCH_LIVE_SMOKE_MAX_THROTTLE: Final = 0.4
RESEARCH_HIGH_SPEED_DEV_MAX_THROTTLE: Final = 1.0
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
    rear_mirror_enabled: bool = False
    pi_trace: bool = False
    camera_diagnostics: bool = False
    run_id: str | None = None
    automation_interaction: ResearchAutomationInteractionConfig | None = None
    persistence: ResearchPersistenceConfig | None = None
    exit_assistance: ResearchExitAssistanceConfig | None = None
    development_validation: bool = False
    p6: P6Config | None = None
    speed_policy: ResearchSpeedPolicy | None = None
    operator_training: bool = False
    operator_nominal_module: bool = False
    operator_clear_noon: bool = False
    activation_speed_hold: bool = False
    traffic_manager_port: int | None = None
    native_tm_diagnostics: NativeTrafficManagerDiagnosticsConfig | None = None
    tsxw_config: TsxwConfig | None = None
    vehicle_audio: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.duration) or self.duration <= 0.0:
            raise ResearchNoAConfigError("duration", "must be finite and > 0")
        if self.operator_nominal_module:
            self._validate_operator_nominal_module()
        if self.operator_training:
            self._validate_operator_training()
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
        if self.traffic_manager_port is not None and not (
            1 <= self.traffic_manager_port <= 65535
        ):
            raise ResearchNoAConfigError(
                "traffic_manager_port", "must be within [1, 65535]"
            )
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
        if self.activation_speed_hold and (
            self.persistence is None
            or self.automation_interaction is None
            or self.speed_policy is None
            or not self.speed_policy.noa_only
        ):
            raise ResearchNoAConfigError(
                "activation_speed_hold",
                "requires NoA-only policy with persisted interaction",
            )
        if self.traffic_manager_port is not None:
            if self.mode is not ResearchNoARunMode.LIVE_SMOKE:
                raise ResearchNoAConfigError(
                    "traffic_manager_port", "requires live smoke mode"
                )
            if self.speed_policy is not None or self.activation_speed_hold:
                raise ResearchNoAConfigError(
                    "traffic_manager_port",
                    "uses Traffic Manager default speed without a custom speed policy",
                )
            if self.control_config.longitudinal.target_speed_kmh is not None:
                raise ResearchNoAConfigError(
                    "target_speed_kmh", "must be unset for Traffic Manager control"
                )
        if self.native_tm_diagnostics is not None and (
            self.mode is not ResearchNoARunMode.LIVE_SMOKE
            or self.traffic_manager_port is None
            or self.persistence is None
        ):
            raise ResearchNoAConfigError(
                "native_tm_diagnostics",
                "requires persisted live Traffic Manager operation",
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
        if self.p6 is not None and self.persistence is None:
            raise ResearchNoAConfigError("p6", "requires persisted study run context")
        if self.speed_policy is not None:
            if (
                self.mode is not ResearchNoARunMode.LIVE_SMOKE
                or self.automation_interaction is None
                or self.persistence is None
            ):
                raise ResearchNoAConfigError(
                    "speed_policy",
                    "requires live smoke with persisted interaction",
                )
            operator_p5 = self.exit_assistance is not None and (
                self.operator_nominal_module or self.operator_training
            )
            excludes_elevated_policy = (
                self.p6 is not None
                or self.development_validation
                or (
                    self.exit_assistance is not None
                    and (
                        self.speed_policy.high_speed_dev_acknowledged or not operator_p5
                    )
                )
            )
            if (
                self.speed_policy.high_speed_dev_acknowledged
                or self.speed_policy.noa_only
            ) and excludes_elevated_policy:
                raise ResearchNoAConfigError(
                    "speed_policy",
                    "elevated speed policies exclude unvalidated P5, P6, and development validation",
                )

    def _validate_live_safety_caps(self) -> None:
        longitudinal = self.control_config.longitudinal
        lateral = self.control_config.lateral
        capped_values = (
            (
                "duration",
                self.duration,
                (
                    RESEARCH_OPERATOR_TRAINING_MAX_DURATION_SECONDS
                    if self.operator_training
                    else (
                        RESEARCH_MODULE_2_NOMINAL_DURATION_SECONDS
                        if self.operator_nominal_module
                        else (
                            RESEARCH_DEVELOPMENT_VALIDATION_MAX_DURATION_SECONDS
                            if self.development_validation
                            else RESEARCH_LIVE_SMOKE_MAX_DURATION_SECONDS
                        )
                    )
                ),
            ),
            (
                "max_throttle",
                longitudinal.max_throttle,
                (
                    RESEARCH_HIGH_SPEED_DEV_MAX_THROTTLE
                    if self.traffic_manager_port is not None
                    or (
                        self.speed_policy is not None
                        and (
                            self.speed_policy.high_speed_dev_acknowledged
                            or self.speed_policy.noa_only
                        )
                    )
                    else RESEARCH_LIVE_SMOKE_MAX_THROTTLE
                ),
            ),
            ("max_brake", longitudinal.max_brake, 0.5),
            ("max_steering", lateral.max_steering, 0.15),
        )
        for name, value, cap in capped_values:
            if value > cap:
                raise ResearchNoAConfigError(name, f"must be <= {cap}")
        if self.traffic_manager_port is None:
            try:
                resolve_speed_policy(
                    longitudinal.target_speed_kmh,
                    self.speed_policy,
                    activation_speed_hold=self.activation_speed_hold,
                )
            except ResearchSpeedPolicyError as error:
                raise ResearchNoAConfigError(error.field, error.requirement) from error

    def _validate_operator_training(self) -> None:
        persistence = self.persistence
        interaction = self.automation_interaction
        exit_config = self.exit_assistance
        valid_context = (
            self.mode is ResearchNoARunMode.LIVE_SMOKE
            and persistence is not None
            and persistence.segment_phase is ExperimentPhase.TRAINING
            and persistence.module is ExperimentModule.MODULE_1
            and interaction is not None
            and interaction.module is ExperimentModule.MODULE_1
            and interaction.condition is persistence.assignment.m1_condition
            and interaction.initial_stage == ExperimentPhase.TRAINING.value
        )
        valid_exit = exit_config is None or exit_config.context is ExitContext.TRAINING
        excludes_other_protocols = self.p6 is None and not self.development_validation
        if not valid_context or not valid_exit or not excludes_other_protocols:
            raise ResearchNoAConfigError(
                "operator_training",
                "requires live Module 1 TRAINING context and validated optional P5",
            )

    def _validate_operator_nominal_module(self) -> None:
        persistence = self.persistence
        interaction = self.automation_interaction
        if persistence is None or interaction is None:
            raise ResearchNoAConfigError(
                "operator_nominal_module",
                "requires persisted assigned interaction context",
            )
        match persistence.module:
            case ExperimentModule.MODULE_1:
                expected_stage = "MODULE_1_RUNNING"
                expected_duration = RESEARCH_MODULE_1_NOMINAL_DURATION_SECONDS
            case ExperimentModule.MODULE_2:
                expected_stage = "MODULE_2_RUNNING"
                expected_duration = RESEARCH_MODULE_2_NOMINAL_DURATION_SECONDS
            case unreachable:
                assert_never(unreachable)
        exit_config = self.exit_assistance
        valid_context = (
            self.mode is ResearchNoARunMode.LIVE_SMOKE
            and interaction.module is persistence.module
            and interaction.condition is persistence.condition
            and interaction.initial_stage == expected_stage
            and persistence.stage_label == expected_stage
            and persistence.segment_phase is None
            and self.duration == expected_duration
            and (
                self.traffic_manager_port is not None
                or (self.speed_policy is not None and self.speed_policy.noa_only)
            )
        )
        if self.exit_assistance is not None and (
            self.exit_assistance.eligibility_open_s is None
            or self.exit_assistance.eligibility_close_s is None
        ):
            raise ResearchNoAConfigError(
                "operator_nominal_module",
                "requires an explicit module eligibility window",
            )
        valid_exit = exit_config is None or (
            exit_config.context is ExitContext.MODULE
            and exit_config.route_id == persistence.assignment.route_id
            and (self.traffic_manager_port is not None or self.activation_speed_hold)
        )
        excludes_other_protocols = (
            not self.operator_training
            and self.p6 is None
            and not self.development_validation
        )
        if not valid_context or not valid_exit or not excludes_other_protocols:
            raise ResearchNoAConfigError(
                "operator_nominal_module",
                "requires exact assigned nominal module context and validated optional P5",
            )

    @property
    def effective_speed_policy(self) -> EffectiveResearchSpeedPolicy:
        return resolve_speed_policy(
            self.control_config.longitudinal.target_speed_kmh,
            self.speed_policy,
            activation_speed_hold=self.activation_speed_hold,
        )


def parse_arguments(argv: Sequence[str] | None = None) -> ResearchNoARunConfig:
    from src.scenario.research_noa_cli import parse_arguments as parse_cli_arguments

    return parse_cli_arguments(argv)
