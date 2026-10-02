from __future__ import annotations

import pygame
import pytest

from src.experiment.automation import AutomationAvailability, DrivingControlMode
from src.scenario.driver_view import DriverViewConfig
from src.vehicle.driving_mode import DrivingMode
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_noa_runtime import make_control_config


def test_runner_composes_owned_hero_map_bundle_and_scheduler() -> None:
    from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        assert session.hero is vehicle
        assert session.bundle.automation_runtime.state.availability is (
            AutomationAvailability.AVAILABLE
        )
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert session.bundle.control_backend.active is False
        assert vehicle.autopilot_enabled is False
        assert viewer.config.initial_driving_mode is DrivingMode.MANUAL
        session.run()
        assert viewer.received_scheduler is session.dry_scheduler

    assert world.spawn_calls == [(world.blueprint, world.map.spawn_point)]
    assert world.blueprints.requests == ["vehicle.mercedes.coupe_2020"]
    assert world.blueprint.attributes == [("role_name", "research_noa")]
    assert vehicle.destroy_count == 1
    assert viewer_factory.created[0].close_count == 1


def test_dry_run_rejects_activation_and_never_applies_control() -> None:
    from src.scenario.research_noa import (
        ResearchNoADryRunActivationError,
        ResearchNoARunConfig,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        viewer.actions = [lambda: None, lambda: None, lambda: None]

        with pytest.raises(ResearchNoADryRunActivationError):
            session.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        session.run()

        assert vehicle.applied_controls == []
        assert vehicle.autopilot_calls == [False]
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert session.bundle.control_backend.active is False


def test_research_driver_view_ignores_legacy_p_key() -> None:
    from src.scenario.research_noa import ResearchDriverView

    hero = FakeHero()
    viewer = ResearchDriverView(
        FakeWorld(),
        hero,
        DriverViewConfig(initial_driving_mode=DrivingMode.MANUAL),
    )

    viewer._handle_mode_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)])

    assert viewer.driving_mode is DrivingMode.MANUAL
    assert hero.autopilot_calls == [False]
    assert hero.control_calls == []
