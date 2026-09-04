from dataclasses import FrozenInstanceError

import pytest

from src.vehicle import MockVehicleClient, VehicleState


def test_vehicle_state_creation():
    state = VehicleState(
        timestamp=12.5,
        speed_kmh=82.0,
        steering=-0.1,
        throttle=0.4,
        brake=0.0,
        lane_id=None,
        indicator="left",
    )

    assert state.timestamp == 12.5
    assert state.speed_kmh == 82.0
    assert state.steering == -0.1
    assert state.throttle == 0.4
    assert state.brake == 0.0
    assert state.lane_id is None
    assert state.indicator == "left"


def test_vehicle_state_is_immutable():
    state = VehicleState(
        timestamp=0.0,
        speed_kmh=0.0,
        steering=0.0,
        throttle=0.0,
        brake=0.0,
        lane_id=1,
        indicator="off",
    )

    with pytest.raises(FrozenInstanceError):
        state.speed_kmh = 50.0


def test_mock_vehicle_client_returns_vehicle_state():
    state = MockVehicleClient().get_state()

    assert isinstance(state, VehicleState)


def test_mock_vehicle_client_is_deterministic():
    client = MockVehicleClient()

    assert client.get_state() == client.get_state()
