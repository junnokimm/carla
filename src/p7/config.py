from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from src.p7.types import P7Error, TriggerSource


def positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        raise P7Error(f"INVALID_DEV_PARAMETER:{name}")


@dataclass(frozen=True, slots=True)
class HazardConfig:
    enabled: bool = True
    trigger_source: TriggerSource = TriggerSource.EGO_ROUTE_STATION
    trigger_station_m: float = 22.0
    lead_initial_gap_m: float = 12.0
    obstacle_station_m: float = 45.0
    lead_speed_mps: float = 3.0
    lane_change_duration_s: float = 3.0
    lateral_onset_m: float = 0.05
    observation_duration_s: float = 5.0
    brake_candidate: float = 0.1
    steer_candidate: float = 0.05
    lead_blueprint: str = "vehicle.mercedes.coupe_2020"
    obstacle_blueprint: str = "static.prop.box01"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not isinstance(self.trigger_source, TriggerSource):
            raise P7Error("INVALID_HAZARD_CONFIG")
        for field in ("trigger_station_m", "lead_initial_gap_m", "obstacle_station_m", "lead_speed_mps",
                      "lane_change_duration_s", "lateral_onset_m", "observation_duration_s",
                      "brake_candidate", "steer_candidate"):
            positive(getattr(self, field), field)
        if self.brake_candidate > 1 or self.steer_candidate > 1:
            raise P7Error("INVALID_RESPONSE_CANDIDATE_THRESHOLD")
        if not self.lead_blueprint.startswith("vehicle."):
            raise P7Error("LEAD_MUST_BE_VEHICLE")
        if self.obstacle_blueprint not in {"static.prop.box01", "static.prop.box02", "static.prop.box03"}:
            raise P7Error("OBSTACLE_MUST_BE_EXPLICIT_INANIMATE_CARGO_CANDIDATE")
        if not self.lead_initial_gap_m < self.obstacle_station_m:
            raise P7Error("INVALID_HAZARD_STATIONS")
        if self.trigger_source is TriggerSource.DEV_LEAD_ROUTE_STATION and not (
            self.lead_initial_gap_m < self.trigger_station_m
            and self.trigger_station_m + self.lead_speed_mps * self.lane_change_duration_s + 5 < self.obstacle_station_m
        ):
            raise P7Error("DEV_LEAD_MUST_CLEAR_BEFORE_CARGO")


@dataclass(frozen=True, slots=True)
class TrafficConfig:
    enabled: bool = True
    seed: int = 7
    vehicle_count: int = 2
    speed_mps: float = 2.0
    exclusion_m: float = 25.0
    vehicle_blueprint: str = "vehicle.audi.a2"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.seed) is not int or type(self.vehicle_count) is not int:
            raise P7Error("INVALID_TRAFFIC_CONFIG")
        if not 0 <= self.vehicle_count <= 12:
            raise P7Error("DEV_TRAFFIC_COUNT_MUST_BE_0_TO_12")
        positive(self.speed_mps, "traffic_speed")
        if self.speed_mps > 5:
            raise P7Error("DEV_TRAFFIC_SPEED_EXCEEDS_5_MPS")
        positive(self.exclusion_m, "traffic_exclusion")
        if not self.vehicle_blueprint.startswith("vehicle."):
            raise P7Error("TRAFFIC_MUST_BE_VEHICLE")


@dataclass(frozen=True, slots=True)
class ProtectedZone:
    x: float
    y: float
    radius_m: float

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y):
            raise P7Error("INVALID_PROTECTED_ZONE")
        positive(self.radius_m, "protected_radius")


@dataclass(frozen=True, slots=True)
class P7Config:
    schema_version: int = 1
    parameter_status: str = "DEV_ONLY"
    hazard: HazardConfig = HazardConfig()
    traffic: TrafficConfig = TrafficConfig()
    corridor_length_m: float = 90.0
    protected_zones: tuple[ProtectedZone, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 or self.parameter_status != "DEV_ONLY":
            raise P7Error("ONLY_VERSION_1_DEV_ONLY_CONFIG_SUPPORTED")
        positive(self.corridor_length_m, "corridor_length")
        if self.corridor_length_m > 200:
            raise P7Error("DEV_CORRIDOR_LENGTH_EXCEEDS_200M")
        if self.corridor_length_m < max(self.hazard.obstacle_station_m + 10,
                                        self.hazard.lead_initial_gap_m + 20 * self.hazard.lead_speed_mps):
            raise P7Error("CORRIDOR_TOO_SHORT_FOR_BOUNDED_SMOKE")


def load_config(path: Path) -> P7Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        hazard = raw.pop("hazard", {})
        if "trigger_source" in hazard:
            hazard["trigger_source"] = TriggerSource(hazard["trigger_source"])
        traffic = raw.pop("traffic", {})
        zones = tuple(ProtectedZone(**zone) for zone in raw.pop("protected_zones", []))
        return P7Config(**raw, hazard=HazardConfig(**hazard), traffic=TrafficConfig(**traffic), protected_zones=zones)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise P7Error(f"INVALID_P7_CONFIG:{error}") from error
