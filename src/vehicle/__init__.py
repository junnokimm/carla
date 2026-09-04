from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VehicleState:
    timestamp: float
    speed_kmh: float
    steering: float
    throttle: float
    brake: float
    lane_id: int | None
    indicator: str


class VehicleClient(Protocol):
    def get_state(self) -> VehicleState:
        """Return the current vehicle telemetry state."""


class MockVehicleClient:
    """Provide fixed telemetry for CARLA-independent development."""

    def get_state(self) -> VehicleState:
        return VehicleState(
            timestamp=0.0,
            speed_kmh=0.0,
            steering=0.0,
            throttle=0.0,
            brake=0.0,
            lane_id=None,
            indicator="off",
        )
