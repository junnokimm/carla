from __future__ import annotations

import carla
import pytest

from src.scenario.driver_view import DriverViewConfig
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    InjectedViewerError,
)
from tests.test_noa_runtime import make_control_config


def test_keyboard_interrupt_during_attach_still_destroys_owned_vehicle() -> None:
    from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner

    class InterruptingDriverView(FakeDriverView):
        def attach(self) -> None:
            raise KeyboardInterrupt

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)

    def viewer_factory(
        world_arg: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: DriverViewConfig,
    ) -> InterruptingDriverView:
        assert world_arg is world
        assert hero is vehicle
        return InterruptingDriverView(config)

    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with pytest.raises(KeyboardInterrupt), runner.session():
        pass

    assert vehicle.destroy_count == 1


def test_viewer_close_error_cannot_prevent_owned_vehicle_destruction() -> None:
    from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner

    class FailingCloseDriverView(FakeDriverView):
        def close(self) -> None:
            raise InjectedViewerError("close")

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)

    def viewer_factory(
        world_arg: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: DriverViewConfig,
    ) -> FailingCloseDriverView:
        assert world_arg is world
        assert hero is vehicle
        return FailingCloseDriverView(config)

    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with pytest.raises(InjectedViewerError, match="close failed"), runner.session():
        pass

    assert vehicle.destroy_count == 1


def test_owned_vehicle_destroy_failure_is_explicit() -> None:
    from src.scenario.research_noa import (
        ResearchNoACleanupError,
        ResearchNoARunConfig,
        ResearchNoARunner,
    )

    class UndestroyableVehicle(FakeResearchVehicle):
        def destroy(self) -> bool:
            self.destroy_count += 1
            return False

    vehicle = UndestroyableVehicle()
    world = FakeResearchWorld(vehicle)
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=FakeDriverViewFactory(),
    )

    with pytest.raises(ResearchNoACleanupError, match="destroy"), runner.session():
        pass

    assert vehicle.destroy_count == 1


def test_dry_run_rejects_non_driving_spawn_before_actor_creation() -> None:
    from src.scenario.research_noa import (
        ResearchNoAPreflightError,
        ResearchNoARunConfig,
        ResearchNoARunner,
    )

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    world.map.waypoint.lane_type = carla.LaneType.Sidewalk
    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=FakeDriverViewFactory(),
    )

    with (
        pytest.raises(ResearchNoAPreflightError, match="Driving lane"),
        runner.session(),
    ):
        pass

    assert world.spawn_calls == []
    assert vehicle.applied_controls == []
