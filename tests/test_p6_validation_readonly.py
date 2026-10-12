import pygame
import pytest

from src.scenario.driver_view import DriverView, DriverViewConfig
from tests.test_driver_view import FakeHero, FakeWorld


@pytest.mark.parametrize(("rear_enabled", "camera_count"), [(False, 3), (True, 4)])
def test_preview_constructor_camera_lifecycle_and_keys_never_control_vehicle(
    rear_enabled: bool, camera_count: int,
) -> None:
    hero, world = FakeHero(), FakeWorld()

    view = DriverView(
        world, hero, DriverViewConfig(rear_mirror_enabled=rear_enabled), read_only=True,
    )
    try:
        view.attach()
        view._handle_mode_events([
            pygame.event.Event(pygame.KEYDOWN, key=key)
            for key in (pygame.K_p, pygame.K_v, pygame.K_z, pygame.K_SPACE)
        ])
        view._apply_manual_control()
        view._reset_manual_control()
        with pytest.raises(RuntimeError, match="read-only"):
            view.run(1)
    finally:
        view.close()

    assert hero.autopilot_calls == []
    assert hero.control_calls == []
    assert len(world.spawn_calls) == camera_count
    assert all(sensor.destroy_count == 1 for sensor in world.sensors)
