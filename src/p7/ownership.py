from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.p7.types import P7Error


class ActorRole(StrEnum):
    LEAD = "lead"
    CARGO = "cargo"
    TRAFFIC = "traffic"
    SENSOR = "sensor"


class ActorHandle(Protocol):
    @property
    def id(self) -> int: ...
    def destroy(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class OwnedActor:
    actor: ActorHandle
    role: ActorRole


class OwnedActors:
    def __init__(self, ego_id: int) -> None:
        self.ego_id = ego_id
        self.actors: dict[int, OwnedActor] = {}

    def add(self, actor: ActorHandle, role: ActorRole) -> None:
        if actor.id == self.ego_id:
            raise P7Error("EGO_OWNERSHIP_FORBIDDEN")
        if actor.id in self.actors:
            raise P7Error("ACTOR_ALREADY_OWNED")
        self.actors[actor.id] = OwnedActor(actor, role)

    def require(self, actor_id: int, role: ActorRole) -> None:
        owned = self.actors.get(actor_id)
        if owned is None or owned.role is not role or actor_id == self.ego_id:
            raise P7Error("ACTOR_ROLE_OWNERSHIP_MISMATCH")

    def close(self, role: ActorRole | None = None) -> tuple[str, ...]:
        failures = []
        for actor_id, owned in reversed(tuple(self.actors.items())):
            if role is not None and owned.role is not role:
                continue
            try:
                if not owned.actor.destroy():
                    failures.append(f"{actor_id}:destroy_returned_false")
                    continue
                del self.actors[actor_id]
            except RuntimeError as error:
                failures.append(f"{actor_id}:{error}")
        return tuple(failures)
