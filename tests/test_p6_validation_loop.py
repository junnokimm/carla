import json
import threading
from types import SimpleNamespace

import pygame

from src.p6.aoi import Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.tobii.protocol import TobiiSampleDecoder
from src.p6.validation import live
from src.p6.validation.mapping import WindowGeometry
from src.p6.validation.run import ValidationRun
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_p6_tobii_protocol import gaze_record


def test_space_at_previous_segment_end_must_not_skip_next_prepare(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    current = [0]
    monkeypatch.setattr(live.time, "monotonic_ns", lambda: current[0])
    monkeypatch.setattr(live.time, "perf_counter", lambda: current[0] / 1e9)
    monkeypatch.setattr("src.scenario.camera_feed.perf_counter", lambda: current[0] / 1e9)
    hero, world = FakeHero(), FakeWorld()
    hero.type_id = "vehicle.fake"
    world.get_settings = lambda: SimpleNamespace(synchronous_mode=False)
    world.get_actor = lambda actor_id: hero
    client = SimpleNamespace(set_timeout=lambda seconds: None, get_world=lambda: world)
    monkeypatch.setattr(live.carla, "Client", lambda host, port: client)
    image = SimpleNamespace(width=1, height=1, frame=1, timestamp=0.0, raw_data=bytes((20, 30, 40, 255)))
    spawn = world.spawn_actor

    def spawn_with_image(*args, **kwargs):
        sensor = spawn(*args, **kwargs)
        def listen(callback):
            sensor.callback = callback
            callback(image)
        sensor.listen = listen
        return sensor

    world.spawn_actor = spawn_with_image
    observed = WindowGeometry(Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1080),
                              Rect(100, 100, 1280, 720), (1280, 720), 1, "FAKE", 96)
    probe = SimpleNamespace(snapshot=lambda hwnd, size: observed, focused=lambda hwnd: True,
                            close=lambda: None)
    monkeypatch.setattr(live, "WindowProbe", lambda: probe)
    monkeypatch.setattr(pygame.display, "get_wm_info", lambda: {"window": 1})
    callback = []
    sample = TobiiSampleDecoder().decode(json.dumps(gaze_record())).sample

    def subscribe(sink):
        callback.append(sink)
        sink(sample)

    source = SimpleNamespace(finished=threading.Event(), subscribe=subscribe,
                             unsubscribe=lambda: None, last_error=None)

    def tick(rate):
        current[0] += 1_000_000_000
        for sensor in world.sensors:
            sensor.callback(image)
        callback[0](sample)

    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=tick))
    def down(key):
        return pygame.event.Event(pygame.KEYDOWN, key=key)

    def up(key):
        return pygame.event.Event(pygame.KEYUP, key=key)
    frames = iter(([down(pygame.K_c), up(pygame.K_c)],
                   [down(pygame.K_SPACE), up(pygame.K_SPACE)], [],
                   [down(pygame.K_SPACE), up(pygame.K_SPACE)], [down(pygame.K_ESCAPE)]))
    monkeypatch.setattr(pygame.event, "get", lambda: next(frames))
    run = ValidationRun(tmp_path / "fake-loop", load_aoi_layout("config/p6_aoi_candidate.json"))
    try:
        live.execute(run, source, live.LiveOptions("fake", 2000, 1, 20, fullscreen=False))
    finally:
        run.sequence.abort(current[0], "TEST_CLEANUP")
        run.finish()

    assert len(run.sequence.windows) == 1
    assert run.sequence.phase == "ABORTED"
    assert run.sequence.target == "REAR_MIRROR"
    assert hero.control_calls == []
    assert hero.autopilot_calls == []
    assert all(sensor.destroy_count == 1 for sensor in world.sensors)
