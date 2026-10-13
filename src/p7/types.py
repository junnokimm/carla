from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.timestamp import TimestampEnvelope

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]


@dataclass(frozen=True, slots=True)
class P7Error(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class HazardState(StrEnum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    LEAD_MOVING_ASIDE = "LEAD_MOVING_ASIDE"
    HAZARD_REVEALED = "HAZARD_REVEALED"
    RESPONDED = "RESPONDED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class HazardOutcome(StrEnum):
    AVOIDED = "AVOIDED"
    STOPPED = "STOPPED"
    COLLISION = "COLLISION"
    ROAD_DEPARTURE = "ROAD_DEPARTURE"
    AUTOMATIC_RESCUE = "AUTOMATIC_RESCUE"
    INCOMPLETE = "INCOMPLETE"
    UNOBSERVABLE = "UNOBSERVABLE"


class TriggerSource(StrEnum):
    EGO_ROUTE_STATION = "EGO_ROUTE_STATION"
    DEV_LEAD_ROUTE_STATION = "DEV_LEAD_ROUTE_STATION"


class EventKind(StrEnum):
    CONFIG = "p7_config"
    ARMED = "hazard_armed"
    TRIGGER = "hazard_trigger"
    LEAD_START = "lead_lane_change_start"
    LATERAL_ONSET = "lead_lateral_onset"
    LEAD_COMPLETE = "lead_lane_change_complete"
    REVEALED = "hazard_revealed"
    BRAKE = "driver_brake_onset"
    STEER = "driver_steer_onset"
    RESPONSE = "hazard_response"
    CANDIDATE_RESPONSE = "hazard_candidate_response"
    SAMPLE = "hazard_sample"
    COLLISION = "collision"
    ROAD_DEPARTURE = "road_departure"
    TERMINATION = "experiment_termination_requested"
    COMPLETE = "hazard_complete"
    ABORT = "hazard_abort"
    SPAWN = "p7_actor_spawn"
    SPAWN_FAILURE = "p7_actor_spawn_failure"
    ACTORS_SETUP = "hazard_actor_setup"
    TRAFFIC_SCAN = "traffic_candidate_scan"
    TRAFFIC_PLAN = "traffic_plan"
    TRAFFIC_REJECT = "traffic_candidate_rejected"
    CLEANUP = "p7_actor_cleanup"


@dataclass(frozen=True, slots=True)
class P7Event:
    kind: EventKind
    timestamp: TimestampEnvelope
    payload: dict[str, Json]


@dataclass(frozen=True, slots=True)
class HazardObservation:
    timestamp: TimestampEnvelope
    ego_station_m: float
    lead_station_m: float
    lead_lateral_m: float
    target_lane_id: int
    lead_complete: bool
    reveal_clear: bool
    distance_m: float
    closing_speed_mps: float
    ego_speed_mps: float
    collision_course: bool | None
    driver_input: DriverInput | None
    input_source: str = "AUTHORITATIVE_DRIVER_INPUT"
    route_valid: bool = True
    actors_alive: bool = True
    obstacle_stationary: bool = True
    lead_path_clear: bool = True
    p5_active: bool = False
    collision_actor_id: int | None = None
    collision_frame: int | None = None
    collision_simulation_s: float | None = None
    road_departure: bool = False
