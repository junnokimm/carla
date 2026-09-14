from types import SimpleNamespace

import pytest

from src.vehicle import VehicleState


class FakeVehicle:
    def __init__(self, role_name: str = "hero", light_state: int = 0) -> None:
        self.attributes = {"role_name": role_name}
        self.id = 42
        self._light_state = light_state

    def get_velocity(self) -> SimpleNamespace:
        return SimpleNamespace(x=3.0, y=4.0, z=0.0)

    def get_control(self) -> SimpleNamespace:
        return SimpleNamespace(steer=-0.2, throttle=0.7, brake=0.1)

    def get_location(self) -> SimpleNamespace:
        return SimpleNamespace(x=1.0, y=2.0, z=3.0)

    def get_light_state(self) -> int:
        return self._light_state


class FakeActors:
    def __init__(self, vehicles: list[FakeVehicle]) -> None:
        self.vehicles = vehicles

    def filter(self, pattern: str) -> list[FakeVehicle]:
        assert pattern == "vehicle.*"
        return self.vehicles


class FakeWorld:
    def __init__(self, vehicles: list[FakeVehicle]) -> None:
        self.actors = FakeActors(vehicles)

    def get_actors(self) -> FakeActors:
        return self.actors

    def get_snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(timestamp=SimpleNamespace(elapsed_seconds=12.5))

    def get_map(self) -> SimpleNamespace:
        return SimpleNamespace(
            get_waypoint=lambda *args, **kwargs: SimpleNamespace(lane_id=-2)
        )


class FakeClient:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.timeout: float | None = None

    def set_timeout(self, timeout: float) -> None:
        self.timeout = timeout

    def get_world(self) -> FakeWorld:
        return self.world


def test_get_state_reads_hero_vehicle_and_converts_speed(monkeypatch):
    from src.vehicle import carla_client

    hero = FakeVehicle(light_state=carla_client.carla.VehicleLightState.LeftBlinker)
    fake_client = FakeClient(FakeWorld([FakeVehicle(role_name="npc"), hero]))
    monkeypatch.setattr(carla_client.carla, "Client", lambda host, port: fake_client)

    client = carla_client.CarlaVehicleClient("127.0.0.1", 2000, timeout=3.0)

    state = client.get_state()

    assert state == VehicleState(
        timestamp=12.5,
        speed_kmh=18.0,
        steering=-0.2,
        throttle=0.7,
        brake=0.1,
        lane_id=-2,
        indicator="left",
    )
    assert fake_client.timeout == 3.0


@pytest.mark.parametrize(
    ("light_state", "indicator"),
    [
        (0, "off"),
        (1, "left"),
        (2, "right"),
        (3, "hazard"),
    ],
)
def test_get_state_maps_indicator_lights(monkeypatch, light_state, indicator):
    from src.vehicle import carla_client

    left = carla_client.carla.VehicleLightState.LeftBlinker
    right = carla_client.carla.VehicleLightState.RightBlinker
    flags = [0, left, right, left | right][light_state]
    fake_client = FakeClient(FakeWorld([FakeVehicle(light_state=flags)]))
    monkeypatch.setattr(carla_client.carla, "Client", lambda host, port: fake_client)

    state = carla_client.CarlaVehicleClient("127.0.0.1", 2000).get_state()

    assert state.indicator == indicator


def test_get_state_fails_when_hero_vehicle_is_missing(monkeypatch):
    from src.vehicle import carla_client

    fake_client = FakeClient(FakeWorld([FakeVehicle(role_name="npc")]))
    monkeypatch.setattr(carla_client.carla, "Client", lambda host, port: fake_client)

    client = carla_client.CarlaVehicleClient("127.0.0.1", 2000)

    with pytest.raises(carla_client.HeroVehicleNotFoundError, match="role_name 'hero'"):
        client.get_state()
