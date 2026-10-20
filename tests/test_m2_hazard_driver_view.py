from types import SimpleNamespace

import carla
import pygame
import pytest

from src.experiment.automation_interaction import DriverInput
from src.scenario import driver_view
from src.scenario.camera_feed import CameraFeed
from tests.test_driver_view import FakeHero, FakeWorld


@pytest.mark.parametrize("ending", ["duration", "escape", "close", "flip_error", "obstacle_collision"])
@pytest.mark.parametrize("clock_failure", [False, True])
def test_active_scene_uses_existing_poll_and_selected_image_without_owning_duration(
    monkeypatch, ending, clock_failure
):
    if clock_failure:
        from src.experiment import observation_clock

        def broken_clock():
            raise OSError("optional observation clock failed")

        monkeypatch.setattr(observation_clock, "time", SimpleNamespace(perf_counter_ns=broken_clock))
    clock = SimpleNamespace(now=0.0, ns=0)
    inputs, frames, trace = [], [], []
    image = lambda frame: SimpleNamespace(
        frame=frame,
        timestamp=frame / 10,
        width=1,
        height=1,
        raw_data=bytes(4),
        transform=carla.Transform(carla.Location(x=frame)),
    )
    feed = CameraFeed("front", SimpleNamespace(id=321))
    feed.receive(image(1))

    def blit(*args):
        feed.receive(image(feed.snapshot().last_sensor_frame + 1))

    monkeypatch.setattr(pygame.display, "init", lambda: None)
    monkeypatch.setattr(pygame.display, "set_mode", lambda size: object())
    monkeypatch.setattr(pygame.display, "set_caption", lambda text: None)
    monkeypatch.setattr(pygame, "quit", lambda: trace.append("quit"))
    monkeypatch.setattr(pygame.image, "frombuffer", lambda *args: object())
    monkeypatch.setattr(pygame.transform, "smoothscale", lambda *args: object())
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: clock.now)

    def monotonic_ns():
        clock.ns += 1
        return int(clock.now * 1e9) + clock.ns

    monkeypatch.setattr(driver_view.time, "monotonic_ns", monotonic_ns)
    monkeypatch.setattr(
        pygame.time,
        "Clock",
        lambda: SimpleNamespace(
            tick=lambda fps: setattr(clock, "now", clock.now + 260)
        ),
    )

    def events():
        if clock.now == 260 and ending in ("escape", "close"):
            return [
                SimpleNamespace(
                    type=pygame.QUIT if ending == "close" else pygame.KEYDOWN,
                    key=pygame.K_ESCAPE,
                )
            ]
        return []

    monkeypatch.setattr(pygame.event, "get", events)

    def flip():
        if clock.now == 260 and ending == "flip_error":
            raise RuntimeError("flip failure")
        trace.append("flip")

    monkeypatch.setattr(pygame.display, "flip", flip)
    monkeypatch.setattr(
        driver_view,
        "DriverViewPresentation",
        lambda *args: SimpleNamespace(
            canvas=SimpleNamespace(blit=blit),
            handle_events=lambda events: None,
            present=lambda: None,
        ),
    )
    view = driver_view.DriverView(FakeWorld(), FakeHero())
    monkeypatch.setattr(view, "_apply_manual_control", lambda: None)
    monkeypatch.setattr(
        view, "_collect_driver_input", lambda events: DriverInput(brake=0.7)
    )
    monkeypatch.setattr(view, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(
        view, "_draw", lambda screen: view._blit_image(screen, feed, (1, 1), (0, 0))
    )

    def after_control(driver, start, completed):
        inputs.append((driver, start, completed))
        if ending == "obstacle_collision":
            scene.collision_stop = SimpleNamespace(wait=lambda: trace.append("collision_notice"))
        return False  # Scene completion cannot stop the 780s M2 loop.

    def after_display(frame):
        assert trace[-1] == "flip"
        assert (frame.flip_completed_perf_counter_ns is None) is clock_failure
        frames.append(frame)

    def clock_hook(*args):
        if clock_failure:
            raise RuntimeError("optional logging hook failed")

    scene = SimpleNamespace(
        started_at=0.0,
        after_control=after_control,
        after_display_completed=after_display,
        record_input_clock=clock_hook,
    )
    view.gaze_end_observer = lambda: trace.append("gaze_end")
    if ending == "flip_error":
        with pytest.raises(RuntimeError, match="flip failure"):
            view.run(780, active_scene=scene)
    else:
        assert view.run(780, active_scene=scene) is (ending not in ("duration", "obstacle_collision"))
    assert trace.index("gaze_end") < trace.index("quit")
    if ending == "obstacle_collision":
        assert len(inputs) == 1
        assert not frames
        assert trace.index("collision_notice") < trace.index("quit")
        return
    assert len(frames) == (3 if ending == "duration" else 1)
    assert all(value.brake == 0.7 and start < end for value, start, end in inputs)
    assert frames[0].frame == 1 and frames[0].camera_transform.location.x == 1
    assert trace[-1] == "quit"
