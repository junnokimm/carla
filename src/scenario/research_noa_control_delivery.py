from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

import carla

from src.scenario.research_noa_types import ResearchNoAClient

type ControlSender = Callable[[carla.VehicleControl], None]


@dataclass(frozen=True, slots=True)
class CheckedControlDeliveryError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return f"checked vehicle control delivery failed: {self.detail}"


def make_checked_control_sender(
    client: ResearchNoAClient,
    actor_id: int,
    *,
    timing_callback: Callable[[float], None] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> ControlSender:
    def send(control: carla.VehicleControl) -> None:
        started_at = clock()
        try:
            responses = client.apply_batch_sync(
                [carla.command.ApplyVehicleControl(actor_id, control)],
                False,
            )
        finally:
            if timing_callback is not None:
                timing_callback(clock() - started_at)
        if len(responses) != 1:
            raise CheckedControlDeliveryError(
                f"expected one response, received {len(responses)}"
            )
        response = responses[0]
        if response.has_error():
            raise CheckedControlDeliveryError(response.error)
        if response.actor_id != actor_id:
            raise CheckedControlDeliveryError(
                f"expected actor {actor_id}, received {response.actor_id}"
            )

    return send
