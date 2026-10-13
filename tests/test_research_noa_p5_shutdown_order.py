from __future__ import annotations

from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.exit_assistance import ExitAssistanceCoordinator
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchWorld,
)
from tests.test_p8_p5_live_drive import p5_runner, p5_services
from tests.test_research_noa_live_safety import SequencedResearchVehicle


def test_p5_finish_failure_occurs_after_brake_and_preserves_manual_speed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = p5_services(tmp_path, "p5-speed-shutdown-order")
    vehicle = SequencedResearchVehicle([0.0, 0.0, 21.0, 7.0])
    world = FakeResearchWorld(vehicle)
    base = p5_runner(services, world)
    viewer_factory = FakeDriverViewFactory()
    brakes_seen_by_finish: list[float] = []

    def configured_viewer_factory(world, hero, config):
        viewer = viewer_factory(world, hero, config)
        viewer.actions = [lambda: None]
        return viewer

    def fail_finish(self, reason, timestamp) -> None:
        brakes_seen_by_finish.append(vehicle.applied_controls[-1].brake)
        raise ValueError("P5 finish failed")

    monkeypatch.setattr(ExitAssistanceCoordinator, "finish", fail_finish)
    runner = ResearchNoARunner(
        base.config,
        client=FakeClient(world),
        viewer_factory=configured_viewer_factory,
        driver_input_source=iter((DriverInput(),)).__next__,
        exit_route_validator=lambda route, carla_map, hero: None,
        borrowed_log=services.borrowed_research_log(),
    )

    with (
        runner.session() as session,
        pytest.raises(ResearchNoAActualSpeedSafetyError) as caught,
    ):
        session.run()

    assert caught.value.source == "MANUAL"
    assert caught.value.actual_speed_kmh == pytest.approx(21.0)
    assert brakes_seen_by_finish == [pytest.approx(0.5)]
    services.close()
