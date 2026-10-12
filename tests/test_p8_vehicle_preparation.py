from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import carla
import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, Stage
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import RegistrationRequest
from src.p8.operator_ui import OperatorApp
from src.p8.operator_ui_types import OperatorUiConfig
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeWorldSnapshot,
)
from tests.test_p8_operator_backend import RunnerEvidence, StrictClock, runtime_config


class SettlingWorld(FakeResearchWorld):
    automatic_gear_first_tick: bool = False

    def wait_for_tick(self, seconds: float) -> FakeWorldSnapshot:
        if self.frame == 0:
            assert self.vehicle.current_control.brake == pytest.approx(0.5)
            assert not self.vehicle.current_control.manual_gear_shift
            if self.automatic_gear_first_tick:
                self.vehicle.current_control.gear = 1
            else:
                self.vehicle.velocity = carla.Vector3D()
        elif self.frame == 1 and self.automatic_gear_first_tick:
            self.vehicle.velocity = carla.Vector3D()
        snapshot = super().wait_for_tick(seconds)
        return replace(snapshot, actor=self.vehicle)


@pytest.mark.parametrize("m2", list(Module2Condition))
@pytest.mark.parametrize("automatic_gear_first_tick", [False, True])
def test_all_p8_stages_prepare_fresh_moving_gear_zero_without_aborting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, m2: Module2Condition,
    automatic_gear_first_tick: bool,
) -> None:
    import pygame

    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    clock = StrictClock()
    evidence = RunnerEvidence([], [], [], [])
    base = runtime_config(clock, evidence)
    logger_ids: list[int] = []
    clock_ids: list[int] = []
    worlds: list[SettlingWorld] = []

    def build(config, borrowed_log):
        assert all(hero.destroy_count == 1 for hero in evidence.vehicles)
        assert all(view.close_count == 1 for view in evidence.viewers)
        vehicle = FakeResearchVehicle(initial_gear=0)
        vehicle.id = 100 + len(evidence.vehicles)
        vehicle.velocity = carla.Vector3D(x=3.905859375 / 3.6)
        evidence.vehicles.append(vehicle)
        run_clock = FakeMonotonicClock()
        world = SettlingWorld(vehicle, monotonic_clock=run_clock)
        world.automatic_gear_first_tick = automatic_gear_first_tick
        worlds.append(world)
        logger_ids.append(id(borrowed_log.logger))
        clock_ids.append(id(borrowed_log.session_clock))

        def viewer_factory(world_arg, hero, view_config):
            viewer = FakeDriverView(view_config)

            def drive() -> None:
                assert hero.current_control.gear > 0
                assert not hero.current_control.manual_gear_shift
                assert not hero.current_control.reverse
                assert hero.velocity.x == 0.0
                assert world.frame == (2 if automatic_gear_first_tick else 3)
                run_clock.advance(config.duration)
                clock.advance(config.duration)

            viewer.actions = [drive]
            evidence.viewers.append(viewer)
            return viewer

        return ResearchNoARunner(
            config, client=FakeClient(world), viewer_factory=viewer_factory,
            monotonic_clock=run_clock, monotonic_ns=clock.monotonic_ns,
            utc_ns=clock.utc_ns, borrowed_log=borrowed_log,
        )

    controller = OperatorController(
        tmp_path, mode=Mode.DEV_REHEARSAL,
        runtime=replace(base, runner_builder=build),
        monotonic_ns=clock.monotonic_ns, utc_ns=clock.utc_ns,
    )
    request = RegistrationRequest(
        "PREPARATION-TEST", "preparation-run",
        m1_condition=Module1Condition.NO_SURT, m2_condition=m2,
    )
    pygame.init()
    try:
        app = OperatorApp(controller, OperatorUiConfig(request, 600.0, None))
        app.start()
        assert controller.snapshot().stage is Stage.EYE_TRACKER_CALIBRATION
        for expected in (Stage.MODULE_1_PREPARE, Stage.REST, Stage.MODULE_2_PREPARE, Stage.SESSION_COMPLETE):
            if controller.snapshot().stage is Stage.REST:
                clock.advance(120.0)
            app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
            app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
            assert controller.snapshot().stage is expected

        assert [run.actual_active_drive_s for run in controller.snapshot().module_runs] == [600.0, 600.0, 780.0]
        assert len({hero.id for hero in evidence.vehicles}) == 3
        assert [hero.destroy_count for hero in evidence.vehicles] == [1, 1, 1]
        assert [view.close_count for view in evidence.viewers] == [1, 1, 1]
        assert logger_ids == [id(controller.session.services.logger)] * 3
        assert clock_ids == [id(controller.session.services.clock)] * 3
        assert all(len(world.wait_for_tick_calls) == (2 if automatic_gear_first_tick else 3) for world in worlds)
        assert controller.session.result["completion_status"] == "DEV_REHEARSAL_PARTIAL_COMPLETE"
        assert controller.session.cleanup_report["completed"] is True
    finally:
        controller.abort("TEST_TEARDOWN")
        pygame.quit()
