from __future__ import annotations

from dataclasses import dataclass

import carla
import pytest

from src.scenario.research_noa_control_delivery import (
    CheckedControlDeliveryError,
    make_checked_control_sender,
)
from tests.research_noa_fakes import (
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
)


def clone_control(control: carla.VehicleControl) -> carla.VehicleControl:
    return carla.VehicleControl(
        throttle=control.throttle,
        steer=control.steer,
        brake=control.brake,
        hand_brake=control.hand_brake,
        reverse=control.reverse,
        manual_gear_shift=control.manual_gear_shift,
        gear=control.gear,
    )


def control_values(control: carla.VehicleControl) -> tuple[float, float, float, bool]:
    return control.throttle, control.brake, control.steer, control.hand_brake


@dataclass(frozen=True, slots=True)
class BatchResponse:
    actor_id: int
    error: str = ""

    def has_error(self) -> bool:
        return bool(self.error)


class BatchRpcError(RuntimeError):
    pass


class ControlledBatchClient:
    def __init__(
        self,
        vehicle: FakeResearchVehicle,
        responses: list[BatchResponse] | None = None,
        rpc_error: BatchRpcError | None = None,
    ) -> None:
        self.world = FakeResearchWorld(vehicle)
        self.responses = responses
        self.rpc_error = rpc_error
        self.calls: list[
            tuple[tuple[carla.command.ApplyVehicleControl, ...], bool]
        ] = []

    def get_world(self) -> FakeResearchWorld:
        return self.world

    def get_trafficmanager(self, port: int) -> FakeTrafficManager:
        return FakeTrafficManager(port)

    def get_client_version(self) -> str:
        return "test-client"

    def get_server_version(self) -> str:
        return "test-server"

    def apply_batch_sync(
        self,
        commands: list[carla.command.ApplyVehicleControl],
        do_tick: bool = False,
    ) -> list[BatchResponse]:
        self.calls.append((tuple(commands), do_tick))
        if self.rpc_error is not None:
            raise self.rpc_error
        if self.responses is not None:
            return self.responses
        return [BatchResponse(command.actor_id) for command in commands]


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ([], "expected one response, received 0"),
        ([BatchResponse(42), BatchResponse(42)], "expected one response, received 2"),
        ([BatchResponse(99)], "expected actor 42, received 99"),
        ([BatchResponse(42, "actor missing")], "actor missing"),
    ],
)
def test_checked_delivery_rejects_invalid_batch_responses_without_retry(
    responses: list[BatchResponse],
    message: str,
) -> None:
    client = ControlledBatchClient(FakeResearchVehicle(), responses)
    sender = make_checked_control_sender(client, 42)

    with pytest.raises(CheckedControlDeliveryError, match=message):
        sender(carla.VehicleControl(brake=0.5))

    assert len(client.calls) == 1
    assert client.calls[0][1] is False


def test_checked_delivery_preserves_rpc_exception_without_retry() -> None:
    expected = BatchRpcError("RPC unavailable")
    client = ControlledBatchClient(FakeResearchVehicle(), rpc_error=expected)

    with pytest.raises(BatchRpcError) as caught:
        make_checked_control_sender(client, 42)(carla.VehicleControl())

    assert caught.value is expected
    assert len(client.calls) == 1


def test_checked_delivery_records_rpc_wait_in_finally_without_changing_batch() -> None:
    expected = BatchRpcError("RPC unavailable")
    client = ControlledBatchClient(FakeResearchVehicle(), rpc_error=expected)
    times = iter((10.0, 10.125))
    durations: list[float] = []

    with pytest.raises(BatchRpcError) as caught:
        make_checked_control_sender(
            client,
            42,
            timing_callback=durations.append,
            clock=lambda: next(times),
        )(carla.VehicleControl())

    assert caught.value is expected
    assert durations == [pytest.approx(0.125)]
    assert len(client.calls) == 1
    assert client.calls[0][1] is False


def test_checked_delivery_accepts_exact_matching_response_without_world_tick() -> None:
    client = ControlledBatchClient(FakeResearchVehicle())
    control = carla.VehicleControl(throttle=0.4)

    make_checked_control_sender(client, 42)(control)

    commands, do_tick = client.calls[0]
    assert do_tick is False
    assert len(commands) == 1
    assert commands[0].actor_id == 42
    assert control_values(commands[0].control) == control_values(control)
    assert client.world.wait_for_tick_calls == []


class CacheSeparatedVehicle(FakeResearchVehicle):
    def __init__(self) -> None:
        super().__init__()
        self.client_cache = carla.VehicleControl()
        self.server_control = carla.VehicleControl()

    def apply_control(self, control: carla.VehicleControl) -> None:
        self.applied_controls.append(clone_control(control))
        if control_values(control) == control_values(self.client_cache):
            return
        self.client_cache = clone_control(control)
        self.server_control = clone_control(control)

    def apply_batch_control(self, control: carla.VehicleControl) -> None:
        self.server_control = clone_control(control)

    def traffic_manager_control(self, control: carla.VehicleControl) -> None:
        self.server_control = clone_control(control)

    def server_frame(self, *, sticky: bool) -> None:
        if not sticky:
            self.server_control = carla.VehicleControl()

    def get_control(self) -> carla.VehicleControl:
        return clone_control(self.server_control)


class CacheSeparatedClient(ControlledBatchClient):
    def __init__(self, vehicle: CacheSeparatedVehicle) -> None:
        super().__init__(vehicle)
        self.vehicle = vehicle

    def apply_batch_sync(
        self,
        commands: list[carla.command.ApplyVehicleControl],
        do_tick: bool = False,
    ) -> list[BatchResponse]:
        self.calls.append((tuple(commands), do_tick))
        for command in commands:
            self.vehicle.apply_batch_control(command.control)
        return [BatchResponse(command.actor_id) for command in commands]


def test_batch_delivery_restores_control_when_client_cache_matches_old_manual_value() -> (
    None
):
    vehicle = CacheSeparatedVehicle()
    client = CacheSeparatedClient(vehicle)
    manual = carla.VehicleControl(throttle=0.4, steer=0.1)
    traffic_manager = carla.VehicleControl(throttle=0.8, steer=-0.2)
    vehicle.apply_control(manual)
    vehicle.traffic_manager_control(traffic_manager)

    vehicle.apply_control(clone_control(manual))
    stale_server = clone_control(vehicle.get_control())
    make_checked_control_sender(client, vehicle.id)(clone_control(manual))

    assert control_values(stale_server) == control_values(traffic_manager)
    assert control_values(vehicle.client_cache) == control_values(manual)
    assert control_values(vehicle.get_control()) == control_values(manual)
    assert len(client.calls) == 1


def test_sticky_server_frame_retains_last_control_while_nonsticky_resets() -> None:
    command = carla.VehicleControl(throttle=0.4)
    nonsticky = CacheSeparatedVehicle()
    sticky = CacheSeparatedVehicle()
    nonsticky.apply_batch_control(command)
    sticky.apply_batch_control(command)

    nonsticky.server_frame(sticky=False)
    sticky.server_frame(sticky=True)

    assert nonsticky.get_control().throttle == 0.0
    assert sticky.get_control().throttle == pytest.approx(0.4)
