from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from math import isfinite
from typing import Protocol, assert_never

from src.experiment.context import ExperimentModule, ExperimentPhase
from src.experiment.session_clock import SessionClock
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.p7.config import HazardConfig
from src.p7.metrics import ResponseMetrics, canonical_hazard_record
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import (
    EventKind,
    HazardObservation,
    HazardOutcome,
    HazardState,
    Json,
    P7Error,
    P7Event,
    TriggerSource,
)


class HazardActors(Protocol):
    def prepare(self, timestamp: TimestampEnvelope) -> None: ...
    def begin_reveal(self, timestamp: TimestampEnvelope) -> None: ...


class HazardScenario:
    def __init__(self, config: HazardConfig, *, module: ExperimentModule, phase: ExperimentPhase,
                 participant_id: str, run_id: str, actors: HazardActors, occurrences: OccurrenceRegistry,
                 event_sink: Callable[[P7Event], None], origin_ns: int,
                 session_clock: SessionClock | None = None) -> None:
        self.config, self.module, self.phase = config, module, phase
        self.participant_id, self.run_id = participant_id, run_id
        self.actors, self.occurrences, self._sink = actors, occurrences, event_sink
        if session_clock is not None and session_clock.baseline_monotonic_ns != origin_ns:
            raise P7Error("SHARED_SESSION_CLOCK_ORIGIN_MISMATCH")
        self.clock = session_clock or SessionClock(HostClockTimestamp(origin_ns, 0))
        self.state = HazardState.IDLE
        self.outcome: HazardOutcome | None = None
        self.metrics = ResponseMetrics(brake_candidate=config.brake_candidate, steer_candidate=config.steer_candidate)
        self.termination_requested = False
        self._lateral_started = self._lead_complete = False

    def arm(self, observation: HazardObservation) -> bool:
        if not self.config.enabled or self.module is not ExperimentModule.MODULE_2:
            return False
        if self.phase is not ExperimentPhase.FINAL_HAZARD_ASSESSMENT:
            return False
        if self.state is not HazardState.IDLE:
            raise P7Error("HAZARD_ALREADY_ARMED_OR_EXECUTED")
        try:
            station = self._station(observation)
            if not observation.route_valid or not isfinite(station) or station >= self.config.trigger_station_m:
                raise P7Error("TRIGGER_LOCATION_INVALID_OR_ALREADY_PASSED")
            self.occurrences.claim(self.participant_id, self.run_id)
            self.actors.prepare(observation.timestamp)
            self.state = HazardState.ARMED
            self._emit(EventKind.ARMED, observation, {"trigger_source": self.config.trigger_source.value,
                       "trigger_station_m": self.config.trigger_station_m, "parameter_status": "DEV_ONLY"})
            self.metrics.observe_input(self.clock.elapsed_seconds(observation.timestamp.host), observation.driver_input)
            return True
        except (P7Error, RuntimeError) as error:
            self.abort(observation, str(error), HazardOutcome.UNOBSERVABLE)
            raise

    def update(self, observation: HazardObservation) -> None:
        if self.state in (HazardState.IDLE, HazardState.COMPLETED, HazardState.ABORTED):
            return
        now = self.clock.elapsed_seconds(observation.timestamp.host)
        collision_observed = observation.collision_actor_id is not None
        if collision_observed or observation.road_departure:
            if collision_observed and observation.road_departure:
                reason = "COLLISION_AND_ROAD_DEPARTURE"
            elif collision_observed:
                reason = EventKind.COLLISION.value
                self.outcome = HazardOutcome.COLLISION
            else:
                reason = EventKind.ROAD_DEPARTURE.value
                self.outcome = HazardOutcome.ROAD_DEPARTURE
            safety_evidence: dict[str, Json] = {
                "other_actor_id": observation.collision_actor_id,
                "sensor_frame": observation.collision_frame,
                "sensor_simulation_s": observation.collision_simulation_s,
                "collision_observed": collision_observed,
                "road_departure_observed": observation.road_departure,
            }
            if collision_observed:
                self._emit(EventKind.COLLISION, observation, safety_evidence)
            if observation.road_departure:
                self._emit(EventKind.ROAD_DEPARTURE, observation, safety_evidence)
            self.termination_requested = True
            self._emit(EventKind.TERMINATION, observation, {"reason": reason,
                       "ego_actuation": "OWNER_REQUIRED_NOT_PERFORMED_BY_P7", **safety_evidence})
            self.abort(observation, reason)
            return
        for valid, reason in ((observation.actors_alive, "ACTOR_LOST"),
                              (observation.obstacle_stationary, "OBSTACLE_MOVED"),
                              (observation.route_valid, "ROUTE_INVALID"),
                              (observation.lead_path_clear, "LEAD_PATH_BLOCKED"),
                              (not observation.p5_active, "P5_CONFLICT")):
            if not valid:
                self.abort(observation, reason, HazardOutcome.UNOBSERVABLE)
                return
        if not all(isfinite(value) for value in (self._station(observation), observation.distance_m,
                                                observation.closing_speed_mps, observation.ego_speed_mps)):
            self.abort(observation, "NONFINITE_OBSERVATION", HazardOutcome.UNOBSERVABLE)
            return
        if self.state is HazardState.ARMED and (observation.reveal_clear or abs(observation.lead_lateral_m) >= self.config.lateral_onset_m):
            self.abort(observation, "PRETRIGGER_LEAD_OR_REVEAL_INVALID", HazardOutcome.UNOBSERVABLE)
            return
        if self.state is HazardState.ARMED and self._station(observation) >= self.config.trigger_station_m:
            self.state = HazardState.TRIGGERED
            self.metrics.trigger_t = now
            self.metrics.start_speed_mps = observation.ego_speed_mps
            self.metrics.start_distance_m = observation.distance_m
            self._emit(EventKind.TRIGGER, observation, {"trigger_source": self.config.trigger_source.value,
                       "reason": "PREDEFINED_STATION_REACHED", "station_m": self._station(observation),
                       "ego_speed_mps": observation.ego_speed_mps, "distance_m": observation.distance_m})
            try:
                self.actors.begin_reveal(observation.timestamp)
            except RuntimeError as error:
                self.abort(observation, f"LEAD_START_FAILED:{error}", HazardOutcome.UNOBSERVABLE)
                return
            self.state = HazardState.LEAD_MOVING_ASIDE
            self._emit(EventKind.LEAD_START, observation, {"target_lane_id": observation.target_lane_id})
        if self.metrics.trigger_t is not None:
            self.metrics.observe_geometry(observation.distance_m, observation.closing_speed_mps, observation.collision_course)
            if not self._lateral_started and abs(observation.lead_lateral_m) >= self.config.lateral_onset_m:
                self._lateral_started = True
                self._emit(EventKind.LATERAL_ONSET, observation, {"lateral_m": observation.lead_lateral_m})
            if self.metrics.reveal_t is None and observation.reveal_clear:
                self.metrics.reveal_t = now
                self.state = HazardState.HAZARD_REVEALED
                self._emit(EventKind.REVEALED, observation, {"condition": "PROJECTED_BOUNDS_DISJOINT_CANDIDATE",
                           "hazard_visible_t": None, "physical_visibility_validated": False})
            if not self._lead_complete and observation.lead_complete:
                self._lead_complete = True
                self._emit(EventKind.LEAD_COMPLETE, observation, {"target_lane_id": observation.target_lane_id})
        for kind in self.metrics.observe_input(now, observation.driver_input):
            if kind is EventKind.RESPONSE:
                self.state = HazardState.RESPONDED
            self._emit(kind, observation, {**self.metrics.snapshot(), "input_source": observation.input_source})
        self._emit(EventKind.SAMPLE, observation, {**self.metrics.snapshot(),
                   "ego_speed_mps": observation.ego_speed_mps, "distance_m": observation.distance_m,
                   "closing_speed_mps": observation.closing_speed_mps,
                   "ego_station_m": observation.ego_station_m, "lead_station_m": observation.lead_station_m,
                   "lead_lateral_m": observation.lead_lateral_m, "target_lane_id": observation.target_lane_id,
                   "lead_complete": observation.lead_complete, "reveal_clear_proxy": observation.reveal_clear,
                   "collision_course": observation.collision_course,
                   "driver_input": None if observation.driver_input is None else asdict(observation.driver_input),
                   "input_source": observation.input_source})
        if self._lead_complete and self.metrics.reveal_t is not None and now - self.metrics.reveal_t >= self.config.observation_duration_s:
            self.state = HazardState.COMPLETED
            self._emit(EventKind.COMPLETE, observation, {**self.metrics.snapshot(), "outcome": "DEV_OBSERVATION_ENDED_NOT_VALIDATED"})

    def abort(self, observation: HazardObservation, reason: str,
              outcome: HazardOutcome | None = None) -> None:
        if self.state in (HazardState.COMPLETED, HazardState.ABORTED):
            return
        self.state = HazardState.ABORTED
        if outcome is not None:
            self.outcome = outcome
        self._emit(EventKind.ABORT, observation, {"reason": reason, **self.metrics.snapshot()})

    def _station(self, observation: HazardObservation) -> float:
        match self.config.trigger_source:
            case TriggerSource.EGO_ROUTE_STATION:
                return observation.ego_station_m
            case TriggerSource.DEV_LEAD_ROUTE_STATION:
                return observation.lead_station_m
            case unreachable:
                assert_never(unreachable)

    def _emit(self, kind: EventKind, observation: HazardObservation, payload: dict[str, Json]) -> None:
        self._sink(P7Event(kind, observation.timestamp, {"hazard_occurrence": f"{self.run_id}:final-hazard",
                   "participant_id": self.participant_id, "run_id": self.run_id,
                   "module": self.module.value, "state": self.state.value, **payload,
                   **canonical_hazard_record(self.outcome),
                   "driver_input": None if observation.driver_input is None else asdict(observation.driver_input),
                   "input_source": observation.input_source,
                   "session_elapsed_s": self.clock.project_elapsed_seconds(observation.timestamp.host),
                   "collision_observed": observation.collision_actor_id is not None,
                   "road_departure_observed": observation.road_departure}))
