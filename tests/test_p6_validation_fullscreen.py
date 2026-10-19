import json
import threading
from types import SimpleNamespace

import pygame
import pytest

from src.p6.aoi import Point, Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.pilot_aoi import classify_screen
from src.p6.tobii.protocol import TobiiSampleDecoder
from src.p6.validation import live
from src.p6.validation.mapping import WindowGeometry, to_render_point
from src.p6.validation.observation import observe
from src.p6.validation.run import ValidationRun
from src.scenario import driver_view_presentation
from src.scenario.driver_view_presentation import aspect_fit_rect
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_p6_tobii_protocol import gaze_record


@pytest.mark.parametrize("surface,physical,origin,dpi", [
    ((1920, 1080), (1920, 1080), (0, 0), 96),
    ((2560, 1440), (2560, 1440), (-2560, -240), 144),
    ((1024, 768), (1024, 768), (0, 0), 96),
    ((3440, 1440), (3440, 1440), (-3440, 0), 96),
    ((1080, 1920), (1080, 1920), (0, 0), 96),
    ((1920, 1200), (2880, 1800), (-2880, 0), 144),
])
def test_fullscreen_validation_matches_pilot_aoi_and_excludes_letterbox(surface, physical, origin, dpi):
    layout = load_aoi_layout("config/p6_aoi_rear_disabled_candidate_v1.json")
    monitor = Rect(*origin, *physical)
    geometry = WindowGeometry(monitor, monitor, monitor, surface, 1, "TEST", dpi)
    viewport = aspect_fit_rect((1280, 720), surface)
    for target, point in (("FORWARD", Point(640, 300)),
                          ("LEFT_MIRROR", Point(144, 520.5)),
                          ("RIGHT_MIRROR", Point(1136, 520.5))):
        normalized = Point((viewport.x + point.x * viewport.width / 1280) / surface[0],
                           (viewport.y + point.y * viewport.height / 720) / surface[1])
        rendered, reason = to_render_point(normalized, geometry, logical_size=(1280, 720))
        assert reason is None
        assert rendered.x == pytest.approx(point.x)
        assert rendered.y == pytest.approx(point.y)

        record = gaze_record()
        sdk = json.loads(record["raw_sdk_json"])
        for eye in ("left", "right"):
            sdk[f"{eye}_gaze_point_on_display_area"] = [normalized.x, normalized.y]
        record["raw_sdk_json"] = json.dumps(sdk)
        sample = TobiiSampleDecoder().decode(json.dumps(record)).sample
        observation = observe(sample, layout, geometry=geometry, operator_confirmed=True)
        assert observation.candidate == target
        assert classify_screen(normalized.x, normalized.y, geometry, layout)[0] == target

    if viewport.x or viewport.y:
        blank = Point(0, 0)
        assert to_render_point(blank, geometry, logical_size=(1280, 720)) == (None, "OUTSIDE_VIEWPORT")
        assert classify_screen(blank.x, blank.y, geometry, layout) == ("NO_AOI", None)


@pytest.mark.parametrize("fullscreen", [True, False])
def test_live_fullscreen_confirm_and_four_targets_preserve_vehicle_and_mapping(monkeypatch, tmp_path, fullscreen):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    current, hwnd = [0], [0]
    monkeypatch.setattr(live.time, "monotonic_ns", lambda: current[0])
    monkeypatch.setattr(live.time, "perf_counter", lambda: current[0] / 1e9)
    monkeypatch.setattr("src.scenario.camera_feed.perf_counter", lambda: current[0] / 1e9)
    monkeypatch.setattr(driver_view_presentation, "current_monitor_bounds", lambda: (0, 0, 1920, 1080))
    hero, world = FakeHero(), FakeWorld()
    hero.type_id = "vehicle.fake"
    world.get_settings = lambda: SimpleNamespace(synchronous_mode=False)
    world.get_actor = lambda actor_id: hero
    monkeypatch.setattr(live.carla, "Client", lambda host, port: SimpleNamespace(
        set_timeout=lambda seconds: None, get_world=lambda: world))
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
    mode_sizes, snapshot_sizes, focused_handles, canvas_sizes = [], [], [], []
    original_set_mode = pygame.display.set_mode

    def set_mode(size, *args, **kwargs):
        mode_sizes.append(size)
        hwnd[0] += 1
        return original_set_mode(size, *args, **kwargs)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    monkeypatch.setattr(pygame.display, "get_wm_info", lambda: {"window": hwnd[0]})

    def snapshot(handle, size):
        assert handle == hwnd[0]
        snapshot_sizes.append(size)
        monitor = Rect(0, 0, 1920, 1080)
        return WindowGeometry(monitor, monitor, Rect(0, 0, *size), size, handle, "TEST", 96)

    def focused(handle):
        focused_handles.append(handle)
        return handle == hwnd[0]

    monkeypatch.setattr(live, "WindowProbe", lambda: SimpleNamespace(
        snapshot=snapshot, focused=focused, close=lambda: None))
    original_draw = live.DriverView.draw_preview

    def draw(view, canvas):
        canvas_sizes.append(canvas.get_size())
        original_draw(view, canvas)

    monkeypatch.setattr(live.DriverView, "draw_preview", draw)
    sample = TobiiSampleDecoder().decode(json.dumps(gaze_record())).sample
    callback, unsubscribed = [], []

    def subscribe(sink):
        callback.append(sink)
        sink(sample)

    source = SimpleNamespace(finished=threading.Event(), subscribe=subscribe,
                             unsubscribe=lambda: unsubscribed.append(True))

    def tick(rate):
        current[0] += 1_000_000_000
        for sensor in world.sensors:
            sensor.callback(image)
        callback[0](sample)

    monkeypatch.setattr(pygame.time, "Clock", lambda: SimpleNamespace(tick=tick))

    def press(key):
        return [pygame.event.Event(pygame.KEYDOWN, key=key), pygame.event.Event(pygame.KEYUP, key=key)]

    frames = iter([
        press(pygame.K_F11) if fullscreen else [], press(pygame.K_F11), press(pygame.K_c),
        press(pygame.K_SPACE), press(pygame.K_F11), [],
        press(pygame.K_SPACE), [], [],
        press(pygame.K_SPACE), [], [],
        press(pygame.K_SPACE), [], [],
    ])
    monkeypatch.setattr(pygame.event, "get", lambda: next(frames))
    run = ValidationRun(tmp_path / "fullscreen", load_aoi_layout("config/p6_aoi_rear_disabled_candidate_v1.json"))
    try:
        options = live.LiveOptions("fake", 2000, 1, 20) if fullscreen else live.LiveOptions(
            "fake", 2000, 1, 20, fullscreen=False)
        live.execute(run, source, options)
    finally:
        run.sequence.abort(current[0], "TEST_CLEANUP")
        run.finish()

    assert run.sequence.phase == "DONE"
    assert [window.target for window in run.sequence.windows] == ["FORWARD", "LEFT_MIRROR", "RIGHT_MIRROR", "MISSING"]
    assert mode_sizes == ([(1280, 720), (1920, 1080), (1280, 720), (1920, 1080)]
                          if fullscreen else [(1280, 720), (1920, 1080)])
    assert snapshot_sizes[-1] == (1920, 1080)
    assert focused_handles[-1] == len(mode_sizes)
    assert set(canvas_sizes) == {(1280, 720)}
    assert hero.control_calls == []
    assert hero.autopilot_calls == []
    assert len(world.spawn_calls) == 3
    assert all(sensor.destroy_count == 1 for sensor in world.sensors)
    assert unsubscribed == [True]
    evidence = json.loads((run.directory / "mapping_evidence.json").read_text())
    assert evidence["window_geometry"]["surface_size"] == [1920, 1080]
    assert evidence["logical_canvas_size"] == [1280, 720]
    assert evidence["presentation_scaling"] == "ASPECT_FIT_LETTERBOX"
