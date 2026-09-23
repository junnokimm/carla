from __future__ import annotations

import pygame
import pytest

from tests.test_driver_view import FakeHero, FakeWorld


def test_initial_front_sensor_uses_original_driver_relative_transform() -> None:
    from src.scenario.driver_view import DriverView

    hero = FakeHero()
    world = FakeWorld()
    viewer = DriverView(world, hero)

    viewer.attach()

    front = world.spawn_calls[0]
    assert front.transform.location.x == pytest.approx(1.4)
    assert front.transform.location.y == pytest.approx(0.0)
    assert front.transform.location.z == pytest.approx(1.3)
    assert front.transform.rotation.pitch == pytest.approx(-2.0)
    assert front.transform.rotation.yaw == pytest.approx(0.0)
    assert front.transform.rotation.roll == pytest.approx(0.0)
    assert front.attached_to is hero


def test_v_respawns_only_front_sensor_with_cockpit_relative_transform() -> None:
    from src.scenario.driver_view import DriverView
    from src.vehicle.driving_mode import DrivingMode

    hero = FakeHero()
    world = FakeWorld()
    viewer = DriverView(world, hero)
    viewer.attach()
    front_feed = viewer.feeds[0]
    old_front, left_mirror, right_mirror = viewer.sensors
    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])
    autopilot_calls = tuple(hero.autopilot_calls)

    viewer._handle_mode_events(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v)]
    )

    new_front, current_left, current_right = viewer.sensors
    spawn = world.spawn_calls[-1]
    assert old_front.stop_count == 1
    assert old_front.destroy_count == 1
    assert old_front.transforms == []
    assert new_front is not old_front
    assert new_front.callback == front_feed.receive
    assert spawn.transform.location.x == pytest.approx(0.2)
    assert spawn.transform.location.y == pytest.approx(-0.35)
    assert spawn.transform.location.z == pytest.approx(1.3)
    assert spawn.transform.rotation.pitch == pytest.approx(-1.5)
    assert spawn.attached_to is hero
    assert current_left is left_mirror
    assert current_right is right_mirror
    assert left_mirror.destroy_count == 0
    assert right_mirror.destroy_count == 0
    assert len(viewer.sensors) == 3
    assert viewer.driving_mode is DrivingMode.MANUAL
    assert tuple(hero.autopilot_calls) == autopilot_calls


def test_second_v_restores_driver_sensor_and_cleanup_owns_active_front() -> None:
    from src.scenario.driver_view import DriverView

    world = FakeWorld()
    viewer = DriverView(world, FakeHero())
    viewer.attach()
    _, left_mirror, right_mirror = viewer.sensors
    toggle = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v)
    viewer._handle_mode_events([toggle])
    cockpit_front = viewer.sensors[0]

    viewer._handle_mode_events([toggle])

    driver_front, current_left, current_right = viewer.sensors
    spawn = world.spawn_calls[-1]
    assert cockpit_front.stop_count == 1
    assert cockpit_front.destroy_count == 1
    assert driver_front is not cockpit_front
    assert spawn.transform.location.x == pytest.approx(0.10)
    assert spawn.transform.location.y == pytest.approx(-0.35)
    assert spawn.transform.location.z == pytest.approx(1.20)
    assert spawn.transform.rotation.pitch == pytest.approx(-1.5)
    assert current_left is left_mirror
    assert current_right is right_mirror
    assert len(viewer.sensors) == 3

    viewer.close()

    assert driver_front.stop_count == 1
    assert driver_front.destroy_count == 1
    assert left_mirror.stop_count == 1
    assert left_mirror.destroy_count == 1
    assert right_mirror.stop_count == 1
    assert right_mirror.destroy_count == 1
