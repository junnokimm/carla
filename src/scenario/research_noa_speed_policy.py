from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Final, assert_never

from src.experiment.automation import DrivingControlMode
from src.experiment.noa_runtime import NoAControlConfig
from src.p7.types import Json

RESEARCH_MAX_ALLOWED_SPEED_KMH: Final = 110.0
RESEARCH_LEGACY_OPERATIONAL_GUARD_KMH: Final = 20.0
RESEARCH_MANUAL_MAX_THROTTLE: Final = 1.0


@dataclass(frozen=True, slots=True)
class ResearchSpeedPolicyError(ValueError):
    field: str
    requirement: str

    def __str__(self) -> str:
        return f"{self.field} {self.requirement}"


@dataclass(frozen=True, slots=True)
class ResearchSpeedPolicy:
    operational_guard_speed_kmh: float
    high_speed_dev_acknowledged: bool = False
    noa_only: bool = False

    def __post_init__(self) -> None:
        guard = self.operational_guard_speed_kmh
        if self.noa_only:
            if guard != RESEARCH_MAX_ALLOWED_SPEED_KMH:
                raise ResearchSpeedPolicyError(
                    "noa_only",
                    "requires operational_guard_speed_kmh exactly 110",
                )
            if self.high_speed_dev_acknowledged:
                raise ResearchSpeedPolicyError(
                    "noa_only",
                    "requires high_speed_dev_acknowledged false",
                )
            return
        if not isfinite(guard) or not 0.0 < guard < RESEARCH_MAX_ALLOWED_SPEED_KMH:
            raise ResearchSpeedPolicyError(
                "operational_guard_speed_kmh",
                "must be finite and within (0, 110)",
            )
        is_high = guard > RESEARCH_LEGACY_OPERATIONAL_GUARD_KMH
        if is_high != self.high_speed_dev_acknowledged:
            raise ResearchSpeedPolicyError(
                "high_speed_dev_acknowledged",
                "must match an operational guard above 20",
            )


@dataclass(frozen=True, slots=True)
class EffectiveResearchSpeedPolicy:
    desired_speed_kmh: float | None
    operational_guard_speed_kmh: float
    profile: str
    noa_only: bool = False
    target_source: str = "FIXED_CONFIG"

    @property
    def max_allowed_speed_kmh(self) -> float:
        return RESEARCH_MAX_ALLOWED_SPEED_KMH

    @property
    def effective_desired_speed_kmh(self) -> float | None:
        if self.desired_speed_kmh is None:
            return None
        return min(self.desired_speed_kmh, self.max_allowed_speed_kmh)

    @property
    def target_pending(self) -> bool:
        return self.desired_speed_kmh is None

    def guard_for_mode(self, mode: DrivingControlMode) -> float | None:
        match mode:
            case DrivingControlMode.MANUAL:
                return None if self.noa_only else self.operational_guard_speed_kmh
            case DrivingControlMode.NOA_ACTIVE:
                return self.operational_guard_speed_kmh
            case unreachable:
                assert_never(unreachable)

    def manifest(self, control_config: NoAControlConfig) -> dict[str, Json]:
        return {
            "desired_speed_kmh": self.desired_speed_kmh,
            "max_allowed_speed_kmh": self.max_allowed_speed_kmh,
            "effective_desired_speed_kmh": self.effective_desired_speed_kmh,
            "target_source": self.target_source,
            "target_pending": self.target_pending,
            "operational_guard_speed_kmh": self.operational_guard_speed_kmh,
            "noa_only": self.noa_only,
            "profile": self.profile,
            "validation_status": "DEV_UNVALIDATED",
            "manual_control_caps": {
                "max_throttle": RESEARCH_MANUAL_MAX_THROTTLE,
                "max_brake": control_config.longitudinal.max_brake,
                "max_steering": control_config.lateral.max_steering,
            },
            "noa_control_caps": {
                "max_throttle": control_config.longitudinal.max_throttle,
                "max_brake": control_config.longitudinal.max_brake,
                "max_steering": control_config.lateral.max_steering,
            },
        }


def resolve_speed_policy(
    desired_speed_kmh: float | None,
    policy: ResearchSpeedPolicy | None,
    *,
    activation_speed_hold: bool = False,
) -> EffectiveResearchSpeedPolicy:
    if activation_speed_hold:
        if desired_speed_kmh is not None:
            raise ResearchSpeedPolicyError(
                "target_speed_kmh",
                "must be pending when activation_speed_hold is enabled",
            )
        if policy is None or not policy.noa_only:
            raise ResearchSpeedPolicyError(
                "activation_speed_hold",
                "requires an explicit NoA-only policy",
            )
        return EffectiveResearchSpeedPolicy(
            None,
            policy.operational_guard_speed_kmh,
            "NOA_ONLY_ACTIVATION_SPEED_HOLD",
            True,
            "ACTIVATION_SNAPSHOT",
        )
    if desired_speed_kmh is None:
        raise ResearchSpeedPolicyError(
            "target_speed_kmh",
            "must be numeric when activation_speed_hold is disabled",
        )
    if (
        not isfinite(desired_speed_kmh)
        or not 0.0 < desired_speed_kmh <= RESEARCH_MAX_ALLOWED_SPEED_KMH
    ):
        raise ResearchSpeedPolicyError(
            "target_speed_kmh",
            "must be finite and within (0, 110]",
        )
    if policy is None:
        if desired_speed_kmh > RESEARCH_LEGACY_OPERATIONAL_GUARD_KMH:
            raise ResearchSpeedPolicyError(
                "target_speed_kmh",
                "above 20 requires an explicit high-speed DEV policy",
            )
        return EffectiveResearchSpeedPolicy(
            desired_speed_kmh,
            RESEARCH_LEGACY_OPERATIONAL_GUARD_KMH,
            "LEGACY_DEV",
            False,
        )
    if desired_speed_kmh >= policy.operational_guard_speed_kmh:
        raise ResearchSpeedPolicyError(
            "operational_guard_speed_kmh",
            "must be greater than target_speed_kmh",
        )
    return EffectiveResearchSpeedPolicy(
        desired_speed_kmh,
        policy.operational_guard_speed_kmh,
        (
            "NOA_ONLY_NOMINAL"
            if policy.noa_only
            else (
                "HIGH_SPEED_DEV_UNVALIDATED"
                if policy.high_speed_dev_acknowledged
                else "LEGACY_DEV"
            )
        ),
        policy.noa_only,
    )
