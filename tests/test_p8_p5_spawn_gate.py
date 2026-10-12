from __future__ import annotations

from math import cos, radians, sin
from pathlib import Path

import pytest

from src.scenario.research_noa_session import ResearchNoAPreflightError
from src.vehicle.carla_exit_route import load_exit_route_manifest
from tests.p8_p5_spawn_fakes import configure_route_spawn
from tests.research_noa_fakes import FakeResearchVehicle, FakeResearchWorld
from tests.test_p8_p5_live_drive import ROUTE_MANIFEST, p5_runner, p5_services


def test_p8_p5_accepts_synthetic_spawn_on_guidance_approach(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "p8-p5-spawn-valid")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)

    with runner.session():
        pass

    assert len(world.spawn_calls) == 1
    services.close()


def test_p8_p5_rejects_off_route_spawn_before_actor_creation(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "p8-p5-spawn-off-route")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    yaw = radians(world.map.spawn_point.rotation.yaw)
    world.map.spawn_point.location.x -= sin(yaw) * world.map.waypoint.lane_width
    world.map.spawn_point.location.y += cos(yaw) * world.map.waypoint.lane_width

    with (
        pytest.raises(ResearchNoAPreflightError, match="projection corridor"),
        runner.session(),
    ):
        pass

    assert world.spawn_calls == []
    services.close()


def test_p8_p5_accepts_approach_before_guidance_for_voluntary_noa(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-before-guidance")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    route = load_exit_route_manifest(ROUTE_MANIFEST).route(services.assignment.route_id)
    configure_route_spawn(world, route, route.points[0].distance_m)

    with runner.session():
        pass

    assert len(world.spawn_calls) == 1
    services.close()


def test_p8_p5_rejects_wrong_direction_spawn_before_actor_creation(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-spawn-heading")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    world.map.spawn_point.rotation.yaw += 180.0

    with (
        pytest.raises(ResearchNoAPreflightError, match="direction"),
        runner.session(),
    ):
        pass

    assert world.spawn_calls == []
    services.close()


def test_p8_p5_rejects_mismatched_lane_identity_before_actor_creation(
    tmp_path: Path,
) -> None:
    services = p5_services(tmp_path, "p8-p5-spawn-identity")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    world.map.waypoint.road_id += 1

    with (
        pytest.raises(ResearchNoAPreflightError, match="lane identity"),
        runner.session(),
    ):
        pass

    assert world.spawn_calls == []
    services.close()


def test_p8_p5_rejects_past_fork_spawn_before_actor_creation(tmp_path: Path) -> None:
    services = p5_services(tmp_path, "p8-p5-spawn-past-fork")
    world = FakeResearchWorld(FakeResearchVehicle())
    runner = p5_runner(services, world)
    route = load_exit_route_manifest(ROUTE_MANIFEST).route(
        runner.config.exit_assistance.route_id
    )
    configure_route_spawn(world, route, route.fork_distance_m)

    with (
        pytest.raises(ResearchNoAPreflightError, match="guidance window"),
        runner.session(),
    ):
        pass

    assert world.spawn_calls == []
    services.close()
