from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import carla
import pygame

from src.experiment.lane_change import LaneChangeDirection


class LaneChangeKeyEvent(Protocol):
    type: int
    key: int
    dict: dict[str, int]


@dataclass(frozen=True, slots=True)
class AcceptedSignalInput:
    direction: LaneChangeDirection | None
    event_ids: frozenset[int]


class LaneChangeRequestInput:
    def __init__(self) -> None:
        self._keys_down: set[int] = set()

    def direction(
        self,
        events: Sequence[LaneChangeKeyEvent],
        light_state: carla.VehicleLightState,
    ) -> LaneChangeDirection | None:
        return self.accept(events, light_state).direction

    def accept(
        self,
        events: Sequence[LaneChangeKeyEvent],
        light_state: carla.VehicleLightState,
    ) -> AcceptedSignalInput:
        left_on = bool(light_state & carla.VehicleLightState.LeftBlinker)
        right_on = bool(light_state & carla.VehicleLightState.RightBlinker)
        requested: LaneChangeDirection | None = None
        accepted: set[int] = set()
        for event in events:
            if event.type not in (pygame.KEYDOWN, pygame.KEYUP):
                continue
            if event.key not in (pygame.K_z, pygame.K_x):
                continue
            if event.type == pygame.KEYUP:
                self._keys_down.discard(event.key)
                continue
            if event.type != pygame.KEYDOWN:
                continue
            if event.dict.get("repeat", 0) or event.key in self._keys_down:
                continue
            self._keys_down.add(event.key)
            accepted.add(id(event))
            if event.key == pygame.K_z:
                if left_on:
                    left_on = False
                else:
                    left_on = True
                    right_on = False
                    requested = LaneChangeDirection.LEFT
            elif right_on:
                right_on = False
            else:
                right_on = True
                left_on = False
                requested = LaneChangeDirection.RIGHT
        if requested is LaneChangeDirection.LEFT and not left_on:
            requested = None
        if requested is LaneChangeDirection.RIGHT and not right_on:
            requested = None
        return AcceptedSignalInput(requested, frozenset(accepted))
