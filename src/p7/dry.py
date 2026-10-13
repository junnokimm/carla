from __future__ import annotations

from pathlib import Path

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.p7.geometry import smooth_lane_change
from src.p7.hazard import HazardScenario
from src.p7.occurrence import OccurrenceRegistry
from src.p7.session import DevelopmentSession
from src.p7.traffic import SpawnCandidate, TrafficScenario
from src.p7.types import EventKind, HazardObservation, HazardState, Json, TriggerSource


class DryActors:
    def __init__(self) -> None:
        self.reveal_s: float | None = None
        self.prepared = False

    def prepare(self, timestamp: TimestampEnvelope) -> None:
        self.prepared = True

    def begin_reveal(self, timestamp: TimestampEnvelope) -> None:
        if timestamp.carla_snapshot is not None:
            self.reveal_s = timestamp.carla_snapshot.simulation_seconds


def execute(session: DevelopmentSession, *, duration: float, occurrences: Path) -> dict[str, Json]:
    actors = DryActors()
    config = session.config
    hazard = HazardScenario(config.hazard, module=session.module, phase=session.segment.phase,
                            participant_id=session.context.participant_id, run_id=session.context.study_run_id,
                            actors=actors, occurrences=OccurrenceRegistry(occurrences),
                            event_sink=session.capture, origin_ns=session.origin.monotonic_ns)
    selected = []
    traffic = TrafficScenario(config.traffic, spawn=lambda candidate: selected.append(candidate.key))
    traffic.start(tuple(SpawnCandidate(str(index), -200 + index * 10, 40) for index in range(15)),
                  ego=(0, 0), protected=config.protected_zones)
    session.emit(EventKind.TRAFFIC_PLAN, session.last_timestamp,
                 {"seed": config.traffic.seed, "count": len(selected), "selected": selected})
    last = None
    for index in range(int(duration * 10) + 1):
        t = index / 10
        ns = session.origin.monotonic_ns + int(t * 1e9)
        timestamp = TimestampEnvelope(HostClockTimestamp(ns, session.origin.utc_ns + int(t * 1e9)),
                                       CarlaSnapshotTimestamp(t, index, ns, ns))
        fraction = 0 if actors.reveal_s is None else smooth_lane_change(t - actors.reveal_s, config.hazard.lane_change_duration_s)[0]
        ego_s = t * 2 if config.hazard.trigger_source is TriggerSource.EGO_ROUTE_STATION else 0
        responded = hazard.metrics.reveal_t is not None and t > hazard.metrics.reveal_t + 0.5
        last = HazardObservation(timestamp, ego_s, config.hazard.lead_initial_gap_m + config.hazard.lead_speed_mps * t,
                                  fraction * 3.5, -2, fraction == 1, fraction > 0.7,
                                  config.hazard.obstacle_station_m - ego_s - 3, 2 if ego_s else 0,
                                  2 if ego_s else 0, True, DriverInput(brake=0.2 if responded else 0),
                                  input_source="DRY_FAKE_DRIVER_INPUT")
        if index == 0:
            hazard.arm(last)
        hazard.update(last)
        if hazard.state in (HazardState.COMPLETED, HazardState.ABORTED):
            break
    if hazard.state not in (HazardState.IDLE, HazardState.COMPLETED, HazardState.ABORTED) and last is not None:
        hazard.abort(last, "DEV_TIME_CAP")
    return {"status": hazard.state.value, "traffic_count": len(selected),
            "hazard_actor_setup_called": actors.prepared, "metrics": hazard.metrics.snapshot(),
            "termination_requested": hazard.termination_requested, "software_test_only": True}
