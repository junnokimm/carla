from __future__ import annotations

import json
from dataclasses import dataclass

import carla

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.scenario.research_traffic_light import (
    ResearchTrafficLightObserver,
    TrafficLightRuntimeMetadata,
)


@dataclass(frozen=True, slots=True)
class Waypoint:
    road_id: int
    section_id: int
    lane_id: int


@dataclass(frozen=True, slots=True)
class Light:
    id: int
    state: carla.TrafficLightState
    affected: list[Waypoint]
    stops: list[Waypoint]

    def get_state(self) -> carla.TrafficLightState:
        return self.state

    def get_affected_lane_waypoints(self) -> list[Waypoint]:
        return self.affected

    def get_stop_waypoints(self) -> list[Waypoint]:
        return self.stops


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: int = 42
    at_light: bool = False
    light: Light | None = None

    def is_at_traffic_light(self) -> bool:
        return self.at_light

    def get_traffic_light(self) -> Light | None:
        return self.light

    def get_traffic_light_state(self) -> carla.TrafficLightState:
        return carla.TrafficLightState.Green


@dataclass(frozen=True, slots=True)
class Authority:
    active: bool


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, TimestampEnvelope]] = []
        self.timestamps = iter((300, 500))

    @property
    def source(self) -> Sink:
        return self

    def get_host_timestamp(self) -> TimestampEnvelope:
        value = next(self.timestamps)
        return TimestampEnvelope(HostClockTimestamp(value, value + 1))

    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None:
        self.events.append((event_type, payload_json, timestamp))


def sample() -> ResearchSnapshotSample:
    timestamp = TimestampEnvelope(
        HostClockTimestamp(100, 200),
        CarlaSnapshotTimestamp(1.5, 12, 90, 95),
    )
    return ResearchSnapshotSample(
        timestamp,
        carla.Transform(),
        39,
        0,
        -3,
        3.5,
        carla.Transform(),
        -4,
        3.5,
        carla.Transform(),
        carla.VehicleControl(throttle=0.2, brake=0.1),
        "off",
        72.0,
    )


ACTIVE = AutomationState(
    AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE
)


def observer(vehicle: Vehicle, sink: Sink) -> ResearchTrafficLightObserver:
    return ResearchTrafficLightObserver(
        vehicle,
        sink,
        Authority(True),
        TrafficLightRuntimeMetadata(8000, "0.9.16", "server-build", False),
    )


def test_red_actor_records_affecting_lane_facts_without_violation_claim() -> None:
    sink = Sink()
    vehicle = Vehicle(
        at_light=True,
        light=Light(
            7,
            carla.TrafficLightState.Red,
            [Waypoint(39, 0, -3)],
            [Waypoint(39, 0, -4)],
        ),
    )

    observer(vehicle, sink).record(sample(), ACTIVE)

    assert len(sink.events) == 1
    payload = json.loads(sink.events[0][1])
    assert payload["observed_red_fact"] is True
    assert payload["affected_lane_relation"] == "TRUE"
    assert payload["stop_waypoint_relation"] == "FALSE"
    assert payload["violation_classification"] == "NOT_CLASSIFIED"
    assert payload["traffic_manager_port_sharing_status"] == "UNKNOWN"
    assert payload["associated_actor_status"] == "UNVERIFIED_ASSOCIATION"
    assert payload["actor_observation_started_host_monotonic_ns"] == 300
    assert payload["actor_observation_completed_host_monotonic_ns"] == 500
    assert payload["ego_carla_frame"] == 12
    assert payload["vehicle_cached_traffic_light_state"] == "Green"
    assert payload["actor_traffic_light_state"] == "Red"
    assert payload["traffic_light_state_difference"] is True
    assert payload["affected_waypoint_lane_ids"] == [-3]
    assert payload["stop_waypoint_lane_ids"] == [-4]
    assert sink.events[0][2].host.monotonic_ns == 500


def test_cached_nonaffecting_red_actor_is_not_dropped_or_called_not_applicable() -> (
    None
):
    sink = Sink()
    vehicle = Vehicle(
        at_light=False,
        light=Light(8, carla.TrafficLightState.Red, [Waypoint(40, 0, -2)], []),
    )

    observer(vehicle, sink).record(sample(), ACTIVE)

    payload = json.loads(sink.events[0][1])
    assert payload["associated_actor_status"] == "UNVERIFIED_ASSOCIATION"
    assert payload["affected_lane_relation"] == "FALSE"
    assert payload["stop_waypoint_relation"] == "UNKNOWN"


def test_no_actor_records_explicit_absence_with_unknown_lane_relations() -> None:
    sink = Sink()

    observer(Vehicle(), sink).record(sample(), ACTIVE)

    payload = json.loads(sink.events[0][1])
    assert payload["associated_actor_status"] == "NO_ASSOCIATED_ACTOR"
    assert payload["actor_traffic_light_state"] is None
    assert payload["observed_red_fact"] is None
    assert payload["affected_lane_relation"] == "UNKNOWN"
