from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from src.p8.native_tm_diagnostics_types import JsonRow
from src.scenario.research_noa_persistence import ResearchNoAPersistence
from src.scenario.research_noa_types import ResearchNoAWorld
from src.scenario.traffic_manager_lane_change import (
    LaneChangeActor,
    LaneChangeActorList,
    empty_rehearsal_inventory_rejection,
)

MAX_EGO_VISIBILITY_WAIT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class _ObservedInventory:
    actors: LaneChangeActorList

    def get_actors(self) -> LaneChangeActorList:
        return self.actors


@dataclass(frozen=True, slots=True)
class RehearsalInventoryPreflight:
    persistence: ResearchNoAPersistence
    timeout_seconds: float
    clock: Callable[[], float]

    def check(self, world: ResearchNoAWorld, ego: LaneChangeActor) -> str | None:
        started = self.clock()
        budget = min(self.timeout_seconds, MAX_EGO_VISIBILITY_WAIT_SECONDS)
        deadline = started + budget
        attempt = 0
        required_frame: int | None = None
        while True:
            attempt += 1
            frame_before: int | None = None
            frame_after: int | None = None
            payload: JsonRow = {
                "ego_actor_id": ego.id,
                "attempt": attempt,
                "timeout_seconds": budget,
                "vehicles": [],
                "prohibited": [],
            }
            try:
                frame_before = world.get_snapshot().frame
                actors = world.get_actors()
                vehicles = list(actors.filter("vehicle.*"))
                prohibited = [
                    candidate
                    for pattern in ("vehicle.*", "walker.*", "static.prop.*")
                    for candidate in actors.filter(pattern)
                    if candidate.id != ego.id
                ]
                payload["vehicles"] = [_vehicle_record(item) for item in vehicles]
                payload["prohibited"] = [
                    {"id": item.id, "type_id": str(getattr(item, "type_id", ""))}
                    for item in prohibited
                ]
                frame_after = world.get_snapshot().frame
                rejection = empty_rehearsal_inventory_rejection(
                    _ObservedInventory(actors), ego
                )
            except (AttributeError, RuntimeError) as error:
                payload.update(
                    frame_before=frame_before,
                    frame_after=frame_after,
                    error=f"{type(error).__name__}:{error}",
                )
                self._record(payload, started, "INVENTORY_QUERY_FAILED")
                return "EMPTY_REHEARSAL_INVENTORY_FAILED"
            payload.update(
                frame_before=frame_before,
                frame_after=frame_after,
                vehicle_count=len(vehicles),
                ego_visible=any(item.id == ego.id for item in vehicles),
            )
            if prohibited or (rejection is not None and vehicles):
                self._record(payload, started, "INVENTORY_MISMATCH")
                return rejection or "EMPTY_REHEARSAL_INVENTORY_REJECTED"
            if required_frame is not None and frame_before < required_frame:
                self._record(payload, started, "FRAME_NOT_ADVANCED")
                return "EMPTY_REHEARSAL_INVENTORY_REJECTED"
            if self.clock() >= deadline:
                self._record(payload, started, "EGO_VISIBILITY_TIMEOUT")
                return "EMPTY_REHEARSAL_INVENTORY_REJECTED"
            if rejection is None:
                self._record(payload, started, "EGO_CONFIRMED", outcome="PASSED")
                return None
            self._record(payload, started, "EGO_NOT_VISIBLE", outcome="WAITING_FOR_EGO")
            remaining = deadline - self.clock()
            if remaining <= 0:
                self._record(payload, started, "EGO_VISIBILITY_TIMEOUT")
                return "EMPTY_REHEARSAL_INVENTORY_REJECTED"
            try:
                snapshot = world.wait_for_tick(remaining)
            except RuntimeError as error:
                payload["error"] = f"{type(error).__name__}:{error}"
                self._record(payload, started, "SNAPSHOT_WAIT_FAILED")
                return "EMPTY_REHEARSAL_INVENTORY_FAILED"
            if snapshot.frame <= frame_after:
                self._record(payload, started, "FRAME_NOT_ADVANCED")
                return "EMPTY_REHEARSAL_INVENTORY_REJECTED"
            if self.clock() >= deadline:
                self._record(payload, started, "EGO_VISIBILITY_TIMEOUT")
                return "EMPTY_REHEARSAL_INVENTORY_REJECTED"
            required_frame = snapshot.frame

    def _record(
        self,
        payload: JsonRow,
        started: float,
        reason: str,
        *,
        outcome: str = "FAILED_CLOSED",
    ) -> None:
        timestamp = self.persistence.source.get_host_timestamp()
        self.persistence.record_phase2_event(
            "p8_native_tm_inventory",
            json.dumps(
                payload
                | {
                    "host_monotonic_ns": timestamp.host.monotonic_ns,
                    "elapsed_seconds": self.clock() - started,
                    "reason": reason,
                    "outcome": outcome,
                    "frame_semantics": "LOCAL_STREAM_BRACKET_NOT_ATOMIC_WORLD_LOCK",
                }
            ),
            timestamp,
        )
        self.persistence.flush_events()


def _vehicle_record(actor: LaneChangeActor) -> JsonRow:
    attributes = getattr(actor, "attributes", {})
    role = attributes.get("role_name") if isinstance(attributes, Mapping) else None
    return {
        "id": actor.id,
        "type_id": str(getattr(actor, "type_id", "")),
        "role_name": role if isinstance(role, str) else None,
    }
