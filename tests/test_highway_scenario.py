from types import SimpleNamespace

import pytest


class FakeBlueprint:
    def __init__(self) -> None:
        self.attributes: list[tuple[str, str]] = []

    def has_attribute(self, name: str) -> bool:
        return name == "role_name"

    def set_attribute(self, name: str, value: str) -> None:
        self.attributes.append((name, value))


class FakeVehicle:
    def __init__(self) -> None:
        self.autopilot_enabled: bool | None = None
        self.destroyed = False

    def set_autopilot(self, enabled: bool) -> None:
        self.autopilot_enabled = enabled

    def destroy(self) -> bool:
        self.destroyed = True
        return True


class FakeWorld:
    def __init__(
        self, vehicle: FakeVehicle | None = None, spawn_error: Exception | None = None
    ) -> None:
        self.blueprint = FakeBlueprint()
        self.spawn_point = SimpleNamespace(name="spawn-point")
        self.vehicle = vehicle
        self.spawn_error = spawn_error
        self.spawn_calls: list[tuple[FakeBlueprint, SimpleNamespace]] = []

    def get_blueprint_library(self) -> SimpleNamespace:
        return SimpleNamespace(filter=lambda pattern: [self.blueprint])

    def get_map(self) -> SimpleNamespace:
        return SimpleNamespace(get_spawn_points=lambda: [self.spawn_point])

    def spawn_actor(
        self, blueprint: FakeBlueprint, spawn_point: SimpleNamespace
    ) -> FakeVehicle:
        self.spawn_calls.append((blueprint, spawn_point))
        if self.spawn_error is not None:
            raise self.spawn_error
        if self.vehicle is None:
            raise RuntimeError("spawn failed")
        return self.vehicle


class FakeClient:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    def get_world(self) -> FakeWorld:
        return self.world


def test_setup_spawns_one_hero_vehicle_enables_autopilot_and_tracks_actor():
    from src.scenario.highway import HighwayScenario

    vehicle = FakeVehicle()
    world = FakeWorld(vehicle)
    scenario = HighwayScenario(client=FakeClient(world))

    hero = scenario.setup()

    assert hero is vehicle
    assert world.spawn_calls == [(world.blueprint, world.spawn_point)]
    assert world.blueprint.attributes == [("role_name", "hero")]
    assert vehicle.autopilot_enabled is True
    assert scenario.actors == (vehicle,)


def test_cleanup_destroys_tracked_actors():
    from src.scenario.highway import HighwayScenario

    vehicle = FakeVehicle()
    scenario = HighwayScenario(client=FakeClient(FakeWorld(vehicle)))
    scenario.setup()

    destroyed_count = scenario.cleanup()

    assert destroyed_count == 1
    assert vehicle.destroyed is True
    assert scenario.actors == ()


def test_cleanup_is_safe_before_setup():
    from src.scenario.highway import HighwayScenario

    scenario = HighwayScenario(client=FakeClient(FakeWorld()))

    assert scenario.cleanup() == 0


def test_setup_reports_spawn_failure_clearly():
    from src.scenario.highway import HighwayScenario, HighwayScenarioError

    world = FakeWorld(spawn_error=RuntimeError("occupied transform"))
    scenario = HighwayScenario(client=FakeClient(world))

    with pytest.raises(HighwayScenarioError, match="Unable to spawn hero vehicle"):
        scenario.setup()


def test_parse_arguments_enables_driver_view():
    from src.scenario.highway import HighwayScenarioRunConfig, parse_arguments

    config = parse_arguments(["--duration", "5", "--driver-view"])

    assert config == HighwayScenarioRunConfig(duration=5.0, driver_view=True)
