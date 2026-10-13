from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_persistence import ResearchSnapshotSample


class TrafficLightWaypoint(Protocol):
    road_id: int
    section_id: int
    lane_id: int


class TrafficLightActor(Protocol):
    id: int

    def get_state(self) -> carla.TrafficLightState: ...

    def get_affected_lane_waypoints(self) -> list[TrafficLightWaypoint]: ...

    def get_stop_waypoints(self) -> list[TrafficLightWaypoint]: ...


class TrafficLightVehicle(Protocol):
    id: int

    def is_at_traffic_light(self) -> bool: ...

    def get_traffic_light(self) -> TrafficLightActor | None: ...

    def get_traffic_light_state(self) -> carla.TrafficLightState: ...


class TrafficLightEventSource(Protocol):
    def get_host_timestamp(self) -> TimestampEnvelope: ...


class TrafficLightEventSink(Protocol):
    @property
    def source(self) -> TrafficLightEventSource: ...

    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None: ...


class TrafficManagerAuthority(Protocol):
    @property
    def active(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class TrafficLightRuntimeMetadata:
    traffic_manager_port: int
    client_version: str
    server_version: str
    synchronous_mode: bool


class ResearchTrafficLightObserver:
    def __init__(
        self,
        vehicle: TrafficLightVehicle,
        persistence: TrafficLightEventSink,
        authority: TrafficManagerAuthority,
        metadata: TrafficLightRuntimeMetadata,
        diagnostics: ResearchNoADiagnostics | None = None,
    ) -> None:
        self._vehicle = vehicle
        self._persistence = persistence
        self._authority = authority
        self._metadata = metadata
        self._diagnostics = diagnostics

    def record(
        self,
        sample: ResearchSnapshotSample,
        state: AutomationState,
    ) -> None:
        performance_started = (
            None if self._diagnostics is None else self._diagnostics.timestamp()
        )
        try:
            self._record_observation(sample, state)
        finally:
            if performance_started is not None:
                self._diagnostics.record_operation_duration(
                    "traffic_light_observation",
                    self._diagnostics.timestamp() - performance_started,
                )

    def _record_observation(
        self,
        sample: ResearchSnapshotSample,
        state: AutomationState,
    ) -> None:
        started = self._persistence.source.get_host_timestamp()
        is_at_light = self._vehicle.is_at_traffic_light()
        vehicle_state = self._vehicle.get_traffic_light_state()
        actor = None
        if is_at_light:
            lookup_started = (
                None if self._diagnostics is None else self._diagnostics.timestamp()
            )
            try:
                actor = self._vehicle.get_traffic_light()
            finally:
                if lookup_started is not None:
                    self._diagnostics.record_operation_duration(
                        "traffic_light_actor_lookup",
                        self._diagnostics.timestamp() - lookup_started,
                    )
        actor_id: int | None = None
        actor_state: str | None = None
        affected_relation = "UNKNOWN"
        stop_relation = "UNKNOWN"
        associated_actor_status = (
            "NO_ASSOCIATED_ACTOR" if is_at_light else "NOT_QUERIED_NOT_AT_LIGHT"
        )
        observed_red: bool | None = None
        affected_lane_ids: list[int] = []
        stop_lane_ids: list[int] = []
        if actor is not None:
            actor_id = int(actor.id)
            state_value = actor.get_state()
            actor_state = state_value.name
            observed_red = state_value is carla.TrafficLightState.Red
            associated_actor_status = "UNVERIFIED_ASSOCIATION"
            ego_identity = (sample.road_id, sample.section_id, sample.lane_id)
            affected_waypoints = actor.get_affected_lane_waypoints()
            stop_waypoints = actor.get_stop_waypoints()
            affected_lane_ids = sorted(
                {int(item.lane_id) for item in affected_waypoints}
            )
            stop_lane_ids = sorted({int(item.lane_id) for item in stop_waypoints})
            affected_relation = _lane_relation(
                ego_identity,
                affected_waypoints,
            )
            stop_relation = _lane_relation(
                ego_identity,
                stop_waypoints,
            )
        completed = self._persistence.source.get_host_timestamp()
        snapshot = sample.timestamp.carla_snapshot
        payload = {
            "vehicle_id": self._vehicle.id,
            "vehicle_is_at_traffic_light": is_at_light,
            "actor_lookup_performed": is_at_light,
            "associated_actor_id": actor_id,
            "actor_traffic_light_state": actor_state,
            "vehicle_cached_traffic_light_state": vehicle_state.name,
            "traffic_light_state_difference": (
                None if actor_state is None else actor_state != vehicle_state.name
            ),
            "associated_actor_status": associated_actor_status,
            "observed_red_fact": observed_red,
            "violation_classification": "NOT_CLASSIFIED",
            "affected_lane_relation": affected_relation,
            "stop_waypoint_relation": stop_relation,
            "affected_waypoint_lane_ids": affected_lane_ids,
            "stop_waypoint_lane_ids": stop_lane_ids,
            "ego_road_id": sample.road_id,
            "ego_section_id": sample.section_id,
            "ego_lane_id": sample.lane_id,
            "speed_kmh": sample.speed_kmh,
            "cached_control_brake": float(sample.control.brake),
            "cached_control_throttle": float(sample.control.throttle),
            "ego_snapshot_host_monotonic_ns": sample.timestamp.host.monotonic_ns,
            "ego_carla_frame": None if snapshot is None else snapshot.frame,
            "ego_carla_simulation_seconds": (
                None if snapshot is None else snapshot.simulation_seconds
            ),
            "actor_observation_started_host_monotonic_ns": started.host.monotonic_ns,
            "actor_observation_completed_host_monotonic_ns": completed.host.monotonic_ns,
            "sample_provenance": "WORLD_SNAPSHOT_ACTOR_PLUS_HOST_RPC_CACHED_CONTROL",
            "traffic_manager_port": self._metadata.traffic_manager_port,
            "traffic_manager_configured_authority": "CARLA_TRAFFIC_MANAGER",
            "traffic_manager_committed_authority": (
                self._authority.active
                and state.control_mode is DrivingControlMode.NOA_ACTIVE
            ),
            "traffic_manager_port_sharing_status": "UNKNOWN",
            "traffic_manager_ignore_lights_percentage_configured": 0.0,
            "traffic_manager_ignore_lights_getter_confirmed": False,
            "world_synchronous_mode": self._metadata.synchronous_mode,
            "client_version": self._metadata.client_version,
            "server_version": self._metadata.server_version,
        }
        serialization_started = (
            None if self._diagnostics is None else self._diagnostics.timestamp()
        )
        try:
            payload_json = json.dumps(
                payload, allow_nan=False, separators=(",", ":"), sort_keys=True
            )
        finally:
            if serialization_started is not None:
                self._diagnostics.record_operation_duration(
                    "json_serialize",
                    self._diagnostics.timestamp() - serialization_started,
                )
        self._persistence.record_phase2_event(
            "traffic_light_observation",
            payload_json,
            completed,
        )


def _lane_relation(
    ego_identity: tuple[int | None, int | None, int | None],
    waypoints: list[TrafficLightWaypoint],
) -> str:
    if not waypoints or any(value is None for value in ego_identity):
        return "UNKNOWN"
    identities = {
        (int(waypoint.road_id), int(waypoint.section_id), int(waypoint.lane_id))
        for waypoint in waypoints
    }
    return "TRUE" if ego_identity in identities else "FALSE"
