"""User-requested M2 v1 scene; tuning values are NOT research thresholds."""

from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True, slots=True)
class MovingHazardConfig:
    seed: int
    preparation_s: float = 6.0
    lane_change_s: float = 3.0
    lead_headway_s: float = 1.5
    minimum_lead_gap_m: float = 12.0
    route_step_m: float = 4.0
    retry_s: float = 1.0
    cleanup_behind_m: float = 80.0
    speed_gain: float = 0.35
    gap_gain: float = 0.15
    steering_gain: float = 1.5
    steering_slew_per_s: float = 0.5
    lead_blueprint: str = "vehicle.mercedes.coupe_2020"
    cargo_blueprint: str = "static.prop.box01"
    # Legacy target-speed fields remain readable for older configurations.
    # Production clear-road exposure uses measured speed, never the road limit.
    target_speed_kmh: float | None = None
    speed_tolerance_kmh: float = 5.0
    stable_speed_s: float = 2.0
    readiness_max_gap_s: float = 0.25
    placement_timeout_s: float = 1.0
    warning_duration_s: float = 5.0
    minimum_exposure_speed_kmh: float = 10.0
    post_exposure_drive_s: float = 10.0

    def __post_init__(self) -> None:
        if type(self.seed) is not int:
            raise ValueError("M2_V1_INTEGER_SEED_REQUIRED")
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, float) and (not isfinite(value) or value <= 0):
                raise ValueError(f"M2_V1_INVALID_TUNING:{field.name}")


SCENARIO_VERSION = "M2_MOVING_HAZARD_V1_20261017"
WINDOW = (400.0, 600.0)
TARGET_TTC_S = 4.0
