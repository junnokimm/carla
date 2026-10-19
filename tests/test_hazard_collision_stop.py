from types import SimpleNamespace

import pygame
import pytest

from src.p8.hazard_collision_stop import HazardCollisionStop
from src.p8.operator_m2_continuation import ContinuingM2Hazard
from tests.test_m2_hazard_v1 import prepare, scene_fixture, tick


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize("target", ["cargo", "lead", "other"])
def test_only_owned_obstacle_contact_latches_once(tmp_path, mode, target):
    scene, world, clock, _events, records, _ = scene_fixture(
        tmp_path, mode=mode, scene_class=ContinuingM2Hazard
    )
    prepare(scene, world)
    actor = {
        "cargo": scene.backend.cargo,
        "lead": scene.backend.lead,
        "other": world.vehicle,
    }[target]
    event = SimpleNamespace(other_actor=actor, frame=world.frame, timestamp=clock())
    scene.backend._collision(event)
    tick(scene, world, 0.1)
    if target == "cargo":
        first = scene.collision_stop
        assert first is not None
        active = scene.elapsed
        scene.backend._collision(event)
        tick(scene, world, 120)
        assert scene.collision_stop is first
        assert scene.elapsed == active
        assert scene.scene_failure is None
    else:
        assert scene.collision_stop is None
    scene.finalize("M2_END")
    if target == "cargo":
        assert records[-1]["driver_response_outcome"] == "COLLISION"
        assert records[-1]["end_reason"] == "HAZARD_COLLISION"
        assert records[-1]["m2_active_elapsed_s"] == active
        assert records[-1]["raw_storage"]["pending_sample_count"] == 0
        assert records[-1]["error"] is None


@pytest.mark.parametrize("held", [False, True])
def test_notice_requires_fresh_space_and_is_rendered_before_confirmation(
    monkeypatch, held
):
    pygame.display.init()
    pygame.display.set_mode((1280, 720))
    stop = object.__new__(HazardCollisionStop)
    stop.record = {"errors": []}
    trace = []
    down = lambda **kw: pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, **kw)
    batches = iter(
        [
            [down(repeat=1)] if not held else [down()],
            [pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)],
            [down()],
        ]
    )
    monkeypatch.setattr(pygame.event, "clear", lambda *_: trace.append("clear"))
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: {pygame.K_SPACE: held})
    monkeypatch.setattr(pygame.display, "flip", lambda: trace.append("shown"))
    monkeypatch.setattr(
        pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda _: None)
    )

    def events():
        assert trace[-1] == "shown"
        return next(batches)

    monkeypatch.setattr(pygame.event, "get", events)
    assert stop._wait_for_confirmation() == "SPACE"
    assert trace.count("shown") == 3


@pytest.mark.parametrize(
    "event,expected",
    [
        (pygame.event.Event(pygame.QUIT), "WINDOW_CLOSE"),
        (pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), "OPERATOR_ESCAPE"),
    ],
)
def test_explicit_operator_exit_still_works(monkeypatch, event, expected):
    pygame.display.init()
    pygame.display.set_mode((1280, 720))
    stop = object.__new__(HazardCollisionStop)
    stop.record = {"errors": []}
    monkeypatch.setattr(pygame.event, "clear", lambda *_: None)
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: {pygame.K_SPACE: False})
    monkeypatch.setattr(pygame.event, "get", lambda: [event])
    assert stop._wait_for_confirmation() == expected
