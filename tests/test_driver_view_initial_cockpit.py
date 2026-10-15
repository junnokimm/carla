from __future__ import annotations

import pygame
import pytest

from src.scenario.driver_view import CameraTransform, DriverView, DriverViewConfig
from src.scenario.research_camera_metadata import _requested_sensor_payload
from src.scenario.research_noa_view import ResearchDriverView, ResearchDriverViewConfig
from tests.test_driver_view import FakeHero, FakeWorld


@pytest.mark.parametrize("front_camera_only", [False, True])
def test_research_attach_starts_cockpit_with_configured_fov_on_both_paths(
    front_camera_only: bool,
) -> None:
    world = FakeWorld()
    config = ResearchDriverViewConfig(
        front_camera_only=front_camera_only,
        cockpit_transform=CameraTransform(x=0.7, y=-0.2, z=1.6, pitch=3.0),
        cockpit_fov=117.0,
    )
    viewer = ResearchDriverView(world, FakeHero(), config)

    viewer.attach()

    front = world.spawn_calls[0]
    assert config.initial_cockpit_view is True
    assert front.attributes["fov"] == "117.0"
    assert front.transform.location.x == pytest.approx(0.7)
    assert front.transform.location.y == pytest.approx(-0.2)
    assert front.transform.location.z == pytest.approx(1.6)
    assert front.transform.rotation.pitch == pytest.approx(3.0)


def test_research_v_cycles_cockpit_to_driver_to_cockpit() -> None:
    world = FakeWorld()
    config = ResearchDriverViewConfig(
        front_camera_only=True,
        cockpit_fov=117.0,
        fov=91.0,
    )
    viewer = ResearchDriverView(world, FakeHero(), config)
    viewer.attach()
    toggle = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v)

    viewer._handle_mode_events([toggle])
    viewer._handle_mode_events([toggle])

    assert [call.attributes["fov"] for call in world.spawn_calls] == [
        "117.0",
        "91.0",
        "117.0",
    ]


def test_base_driver_view_keeps_driver_as_initial_view() -> None:
    world = FakeWorld()
    viewer = DriverView(
        world,
        FakeHero(),
        DriverViewConfig(fov=91.0, cockpit_fov=117.0),
    )

    viewer.attach()

    assert viewer._config.initial_cockpit_view is False
    assert world.spawn_calls[0].attributes["fov"] == "91.0"


def test_initial_cockpit_metadata_uses_actual_requested_configuration() -> None:
    config = ResearchDriverViewConfig(
        cockpit_transform=CameraTransform(x=0.7, y=-0.2, z=1.6, pitch=3.0),
        cockpit_fov=117.0,
    )
    payload = _requested_sensor_payload(config, "front")
    assert payload["fov"] == 117.0
    assert payload["vehicle_relative_mount"]["x"] == 0.7
