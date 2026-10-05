from __future__ import annotations

from dataclasses import dataclass

from src.scenario.research_noa import (
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
    ResearchSmokeReport,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)


@dataclass(frozen=True, slots=True)
class TransmissionFixture:
    vehicle: FakeResearchVehicle
    world: FakeResearchWorld
    viewer_factory: FakeDriverViewFactory
    runner: ResearchNoARunner
    clock: FakeMonotonicClock


def make_transmission_fixture(vehicle: FakeResearchVehicle) -> TransmissionFixture:
    clock = FakeMonotonicClock()
    world = FakeResearchWorld(vehicle, monotonic_clock=clock)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
    )
    return TransmissionFixture(vehicle, world, viewer_factory, runner, clock)


def run_live_frames(
    fixture: TransmissionFixture,
    frame_count: int,
) -> ResearchSmokeReport:
    with fixture.runner.session() as session:
        fixture.viewer_factory.created[0].actions = [lambda: None] * frame_count
        report = session.run()
        assert report is not None
        return report
