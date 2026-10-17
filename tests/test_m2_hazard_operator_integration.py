from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, Stage
from src.p8.operator_cli import _runtime_config, build_parser
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import DevOutcome, RegistrationRequest
from src.p8.operator_ui import OperatorApp
from src.scenario.camera_feed import DisplayedCameraFrame
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.m2_hazard_fakes import TM, World, front_feed
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeMonotonicClock,
    FakeTrafficManagerRuntimeAttestor,
)
from tests.test_p8_operator_backend import StrictClock, advance_to


@pytest.mark.parametrize(
    "condition", [Module2Condition.MANUAL, Module2Condition.NOA_L2]
)
@pytest.mark.parametrize("termination", ["normal", "user_exit", "error", "collision"])
def test_cli_config_actual_runner_session_and_physical_backend_share_m2_lifetime(
    tmp_path, condition, termination
):
    args = build_parser().parse_args(
        [
            "--participant-id",
            "M2-INTEGRATION",
            "--run-id",
            "M2-INTEGRATION-RUN",
            "--m1",
            "NO_SURT",
            "--m2",
            condition.value,
            "--ack-dev-live",
            "--spawn-index",
            "344",
            "--tsxw",
            "--tsxw-index",
            "0",
            "--tsxw-steering-deadzone",
            "0.02",
            "--m2-hazard-v1",
            "--m2-hazard-seed",
            "42",
        ]
    )
    base = _runtime_config(args)
    assert base.m2_hazard_v1.seed == 42
    host = StrictClock()
    worlds, views, scenes, attestations, preparations = [], [], [], [], []

    def build(config, borrowed):
        clock = FakeMonotonicClock()
        world = World(monotonic_clock=clock)
        tm = TM()
        worlds.append(world)

        class View(FakeDriverView):
            def __init__(self, view_config):
                super().__init__(view_config)
                self.feeds = [front_feed(world.vehicle)]

            def prepare_input(self):
                assert self.config.automation_state_source().control_mode.value == "MANUAL"
                assert not any(
                    enabled for enabled, _ in world.vehicle.autopilot_port_calls
                )
                preparations.append(config.automation_interaction.initial_stage)
                clock.advance(17.0)
                host.advance(17.0)

            def run(
                self,
                duration,
                scheduler=None,
                diagnostics=None,
                input_observer=None,
                continuation_factory=None,
                active_scene=None,
            ):
                assert continuation_factory is None
                if active_scene is None:
                    world.step(duration)
                    host.advance(duration)
                    input_observer.update(DriverInput())
                    scheduler.update()
                    input_observer.after_control_applied()
                    return False
                scenes.append(active_scene)
                assert controller.session.state is Stage.MODULE_2_RUNNING
                assert active_scene.started_at == clock()
                origin = clock()
                times = (
                    [active_scene.planned - 6]
                    + [active_scene.planned - 6 + (i + 1) * 0.1 for i in range(220)]
                    + [700, 779.9]
                )
                for index, elapsed in enumerate(times):
                    dt = origin + elapsed - clock()
                    world.step(dt, ego_y=-3.5 if active_scene.first_geometry else 0)
                    host.advance(dt)
                    # Genuine interaction boundary: driver override after exposure.
                    driver = DriverInput(
                        brake=0.3 if active_scene.first_geometry else 0
                    )
                    polled_start = host.monotonic_ns()
                    polled_end = host.monotonic_ns()
                    input_observer.update(driver)
                    scheduler.update()
                    input_observer.after_control_applied()
                    active_scene.after_control(driver, polled_start, polled_end)
                    snapshot = world.get_snapshot()
                    active_scene.after_display_completed(
                        DisplayedCameraFrame(
                            900,
                            snapshot.frame,
                            snapshot.timestamp.elapsed_seconds,
                            clock(),
                            host.monotonic_ns(),
                            self.feeds[0].sensor.get_transform(),
                        )
                    )
                    diagnostics.record_driver_loop_duration(0.0)
                    assert controller.session.state is Stage.MODULE_2_RUNNING
                    if index == 100 and termination != "normal":
                        if termination == "user_exit":
                            return True
                        if termination == "collision":
                            active_scene.backend._sensor.callback(
                                SimpleNamespace(
                                    other_actor=world.vehicle,
                                    frame=world.frame,
                                    timestamp=clock(),
                                )
                            )
                            world.step(0.1)
                            input_observer.after_control_applied()
                            active_scene.after_control(driver, polled_start, polled_end)
                        raise RuntimeError("injected offline error")
                dt = origin + duration - clock()
                world.step(dt, ego_speed=0)
                host.advance(dt)
                assert active_scene.first_geometry and active_scene.first_display
                return False

        def viewer_factory(world_arg, hero, view_config):
            assert view_config.vehicle_audio_source is None
            viewer = View(view_config)
            views.append(viewer)
            return viewer

        attestor = FakeTrafficManagerRuntimeAttestor()
        attestations.append(attestor)
        return ResearchNoARunner(
            config,
            client=FakeClient(world, tm),
            viewer_factory=viewer_factory,
            driver_input_source=lambda: DriverInput(),
            monotonic_clock=clock,
            monotonic_ns=host.monotonic_ns,
            utc_ns=host.utc_ns,
            borrowed_log=borrowed,
            tm_runtime_attestor=attestor,
        )

    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=replace(base, runner_builder=build),
        monotonic_ns=host.monotonic_ns,
        utc_ns=host.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            "M2-INTEGRATION",
            "M2-INTEGRATION-RUN",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=condition,
        )
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module()
    assert not scenes and all(len(world.actors) == 1 for world in worlds)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(3 if termination == "normal" else 4):
        controller.advance()
    if termination == "normal":
        # Exercise the actual operator post-drive progression too: v1 returns
        # in MODULE_2_RUNNING, unlike the historical post-780 continuation.
        app = object.__new__(OperatorApp)
        app.controller = controller
        app.config = SimpleNamespace(drive_duration_s=None)
        app._restore_display = lambda **kwargs: None
        app._run_module(Stage.MODULE_2_PREPARE)
        result = controller._operator_components().runs[-1]
        assert result.actual_active_drive_s == pytest.approx(780)
        assert result.window_complete
        assert controller.session.state is Stage.SESSION_COMPLETE
        assert controller._operator_components().hazard_loop is None
    else:
        with pytest.raises(RuntimeError):
            controller.execute_current_module()
        assert controller.session.closed
    assert len(scenes) == 1 and scenes[0].closed
    assert preparations == [
        Stage.TRAINING.value,
        Stage.MODULE_1_RUNNING.value,
        Stage.MODULE_2_RUNNING.value,
    ]
    record = controller._operator_components().hazard_observation
    assert record["provenance"]["client"] == "INJECTED_TEST_CLIENT"
    assert record["cleanup"] == {"failures": [], "remaining_actor_ids": []}
    assert record["partial_data"] is (termination != "normal")
    assert all(view.attach_count == view.close_count == 1 for view in views)
    assert all(world.vehicle.destroy_count == 1 for world in worlds)
    assert all(attestor.calls for attestor in attestations)
    assert (
        scenes[0].session.bundle.automation_runtime.state.control_mode.value == "MANUAL"
    )
    assert controller._done_claim().participant_ready is False
