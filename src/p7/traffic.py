from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from math import hypot

from src.p7.config import ProtectedZone, TrafficConfig
from src.p7.types import P7Error


@dataclass(frozen=True, slots=True)
class SpawnCandidate:
    key: str
    x: float
    y: float


def select_candidates(candidates: tuple[SpawnCandidate, ...], config: TrafficConfig, *,
                      ego: tuple[float, float], protected: tuple[ProtectedZone, ...]) -> tuple[SpawnCandidate, ...]:
    if not config.enabled or config.vehicle_count == 0:
        return ()
    ordered = sorted(candidates, key=lambda item: (item.key, item.x, item.y))
    if len({item.key for item in ordered}) != len(ordered):
        raise P7Error("DUPLICATE_TRAFFIC_CANDIDATE_ID")
    random.Random(config.seed).shuffle(ordered)
    selected: list[SpawnCandidate] = []
    for candidate in ordered:
        if hypot(candidate.x - ego[0], candidate.y - ego[1]) < config.exclusion_m:
            continue
        if any(hypot(candidate.x - zone.x, candidate.y - zone.y) <= zone.radius_m for zone in protected):
            continue
        if any(hypot(candidate.x - other.x, candidate.y - other.y) < config.exclusion_m for other in selected):
            continue
        selected.append(candidate)
        if len(selected) == config.vehicle_count:
            return tuple(selected)
    raise P7Error("INSUFFICIENT_CLEAR_TRAFFIC_CANDIDATES")


class TrafficScenario:
    def __init__(self, config: TrafficConfig, *, spawn: Callable[[SpawnCandidate], None]) -> None:
        self.config, self._spawn = config, spawn
        self.selected: tuple[SpawnCandidate, ...] = ()
        self._started = False

    def start(self, candidates: tuple[SpawnCandidate, ...], *, ego: tuple[float, float],
              protected: tuple[ProtectedZone, ...]) -> None:
        if self._started:
            raise P7Error("TRAFFIC_ALREADY_STARTED")
        self._started = True
        self.selected = select_candidates(candidates, self.config, ego=ego, protected=protected)
        for candidate in self.selected:
            self._spawn(candidate)
