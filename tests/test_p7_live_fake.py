from pathlib import Path
from types import SimpleNamespace

import carla
import pygame
import pytest

from src.experiment.context import ExperimentModule
from src.experiment.timestamp import HostClockTimestamp
from src.p7 import live
from src.p7.config import load_config
from src.p7.session import DevelopmentSession
from tests.p7_carla_fakes import Actor, World


@pytest.mark.parametrize("failed_blueprint", [None, "static.prop.box01", "vehicle.mercedes.coupe_2020"])
def test_entire_live_adapter_with_fake_world_never_mutates_ego_and_cleans_owned_actors(monkeypatch, tmp_path, failed_blueprint):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    elapsed = [0.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(live.time, "monotonic_ns", lambda: 1_000_000_000 + round(elapsed[0] * 1e9))
    monkeypatch.setattr(live.time, "perf_counter", lambda: elapsed[0])
    monkeypatch.setattr("src.scenario.camera_feed.perf_counter", lambda: elapsed[0])
    world, ego, foreign = World(fail_type=failed_blueprint), Actor(1), Actor(2, transform=carla.Transform(carla.Location(x=500, y=100)))
    ego.get_control = lambda: carla.VehicleControl()
    world.get_actor = lambda actor_id: ego if actor_id == 1 else None
    world.get_settings = lambda: SimpleNamespace(synchronous_mode=False)
    world.get_snapshot = lambda: SimpleNamespace(frame=round(elapsed[0] * 10), timestamp=SimpleNamespace(elapsed_seconds=elapsed[0]))
    world.get_actors = lambda: SimpleNamespace(filter=lambda pattern: [actor for actor in (ego, foreign, *world.spawned)
                                                                       if actor.is_alive and actor.type_id.startswith("vehicle.")])
    client = SimpleNamespace(set_timeout=lambda timeout: None, get_world=lambda: world)
    monkeypatch.setattr(live.carla, "Client", lambda host, port: client)
    cameras = []

    def image():
        return SimpleNamespace(width=1, height=1, frame=round(elapsed[0] * 10),
                               timestamp=elapsed[0], raw_data=bytes((30, 40, 50, 255)))

    def spawn_sensor(blueprint, transform, attach_to):
        sensor = Actor(100 + len(world.spawned), blueprint.id, transform)
        sensor.stop = lambda: sensor.mutations.append("stop")
        def listen(callback):
            sensor.callback = callback
            if blueprint.id.startswith("sensor.camera"):
                cameras.append(sensor)
                callback(image())
        sensor.listen = listen
        world.spawned.append(sensor)
        return sensor

    world.spawn_actor = spawn_sensor

    def tick(rate):
        elapsed[0] = round(elapsed[0] + 0.1, 4)
        for camera in cameras:
            camera.callback(image())

    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=tick))
    monkeypatch.setattr(pygame.event, "get", list)
    config = load_config(Path("config/p7_dev_static_smoke.json"))
    session = DevelopmentSession(tmp_path / "fake-live", config=config, module=ExperimentModule.MODULE_2,
                                 participant_id="DEV-FAKE-WORLD", origin=HostClockTimestamp(1_000_000_000, 1), dry=True)
    try:
        summary = live.execute(session, live.LiveOptions("fake", 2000, 1, 20, tmp_path / "claims"))
    finally:
        session.logger.close()

    assert summary["status"] == ("COMPLETED" if failed_blueprint is None else "ABORTED"), summary
    assert bool(summary["errors"]) == (failed_blueprint is not None)
    assert ego.mutations == foreign.mutations == []
    assert ego.is_alive and foreign.is_alive
    assert world.spawned and all(not actor.is_alive for actor in world.spawned)
    assert summary["metrics"]["hazard_visible_t"] is None
