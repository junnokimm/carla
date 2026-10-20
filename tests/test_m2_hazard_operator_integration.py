import json
from dataclasses import replace
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
import pygame

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitAssistanceCoordinator
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
from tests.test_p5_exit_assistance import ReferenceRecorder, coordinator, observation


@pytest.mark.parametrize(
    "condition", [Module2Condition.MANUAL, Module2Condition.NOA_L2]
)
@pytest.mark.parametrize("termination", ["normal", "user_exit", "error", "collision", "scene_failure", "obstacle_collision", "obstacle_collision_cleanup_error", "exit_not_presented"])
def test_cli_config_actual_runner_session_and_physical_backend_share_m2_lifetime(
    tmp_path, condition, termination, monkeypatch
):
    collision_end = termination.startswith("obstacle_collision")
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
    # Deterministic fake world drives at 12 m/s; production uses the road limit.
    base = replace(base, m2_hazard_v1=replace(base.m2_hazard_v1, target_speed_kmh=43.2))
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

            def freeze_performance_measurement_window(self):
                pass

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
                # The fake driver view now presents and passes an M2 exit
                # before exercising the real production hazard/stop lifecycle.
                if termination != "exit_not_presented":
                    template = coordinator(ExperimentModule.MODULE_2, condition, ReferenceRecorder([]))
                    active_scene.session.exit_assistance = ExitAssistanceCoordinator(
                        replace(template.config, signal_guided_exit=True),
                        ReferenceRecorder([]), active_scene.session.persistence.record_exit,
                    )
                times = (
                    [100, 200, active_scene.planned - 6]
                    + [active_scene.planned - 6 + (i + 1) * 0.1 for i in range(220)]
                    + [700, 779.9]
                )
                for index, elapsed in enumerate(times):
                    dt = origin + elapsed - clock()
                    world.step(dt, ego_y=-3.5 if active_scene.first_geometry else 0)
                    host.advance(dt)
                    # Genuine interaction boundary: driver override after exposure.
                    driver = DriverInput(
                        brake=0.3 if active_scene.first_geometry and not collision_end else 0
                    )
                    polled_start = host.monotonic_ns()
                    polled_end = host.monotonic_ns()
                    input_observer.update(driver)
                    scheduler.update()
                    input_observer.after_control_applied()
                    exit_subject = active_scene.session.exit_assistance
                    if exit_subject is not None and elapsed in (100, 200):
                        obs = replace(
                            observation(250 if elapsed == 100 else 1400, frame=world.frame),
                            timestamp=active_scene.services.stamp(),
                        )
                        if elapsed == 200:
                            obs = replace(obs, road_id=1184, lane_id=-4,
                                          route_reference_distance_m=10,
                                          route_reference_half_width_m=2)
                        exit_subject.observe(obs)
                        if elapsed == 100:
                            state = active_scene.session.bundle.automation_runtime.state
                            exit_subject.mark_presented(
                                exit_subject.prepare_presentation(state), state,
                                active_scene.services.stamp(),
                            )
                    active_scene.after_control(driver, polled_start, polled_end)
                    pygame.font.init()
                    active_scene.draw_warning(pygame.Surface((1280, 720)))
                    active_scene.after_warning_flip(host.monotonic_ns(), host.monotonic_ns())
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
                    fault_index = 100
                    if index == fault_index and termination not in ("normal", "exit_not_presented"):
                        if termination == "user_exit":
                            return True
                        if termination == "collision" or collision_end:
                            active_scene.backend._sensor.callback(
                                SimpleNamespace(
                                    other_actor=active_scene.backend.cargo if collision_end else world.vehicle,
                                    frame=world.frame,
                                    timestamp=clock(),
                                )
                            )
                            world.step(0.1)
                            input_observer.after_control_applied()
                            active_scene.after_control(driver, polled_start, polled_end)
                            if collision_end:
                                stop = active_scene.collision_stop
                                assert stop is not None

                                def confirm():
                                    assert not active_scene.session.bundle.traffic_manager_backend.active
                                    assert world.vehicle.autopilot_port_calls[-1] == (False, 8000)
                                    assert world.vehicle.get_control().brake == 1
                                    assert world.vehicle.get_control().hand_brake
                                    assert world.vehicle.destroy_count == 0
                                    clock.advance(120)
                                    host.advance(120)
                                    assert world.vehicle.is_alive
                                    return "SPACE"

                                monkeypatch.setattr(stop, "_wait_for_confirmation", confirm)
                                stop.wait()
                                if termination == "obstacle_collision_cleanup_error":
                                    original_shutdown = active_scene.session._shutdown_live

                                    def fail_shutdown(shutdown=original_shutdown):
                                        shutdown()
                                        raise RuntimeError("injected post-confirmation shutdown failure")

                                    monkeypatch.setattr(active_scene.session, "_shutdown_live", fail_shutdown)
                                return False
                        elif termination == "scene_failure":
                            cargo = active_scene.backend.cargo
                            pose = cargo.get_transform()
                            pose.location.x += 1
                            cargo.set_transform(pose)
                            world.step(0.1)
                            host.advance(0.1)
                            input_observer.update(driver)
                            scheduler.update()
                            input_observer.after_control_applied()
                            active_scene.after_control(driver, polled_start, polled_end)
                            assert active_scene.scene_failure is not None
                        else:
                            raise RuntimeError("injected offline error")
                dt = origin + duration - clock()
                world.step(dt, ego_speed=0)
                host.advance(dt)
                if termination == "exit_not_presented":
                    assert active_scene.first_display is None and active_scene.backend.cargo is None
                    assert active_scene.state == "NOT_OCCURRED" and active_scene.scene_failure is None
                elif termination != "scene_failure":
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
    for _ in range(3 if termination in ("normal", "collision", "scene_failure", "exit_not_presented") or collision_end else 4):
        controller.advance()
    if termination in ("normal", "collision", "scene_failure", "exit_not_presented") or collision_end:
        # Exercise the actual operator post-drive progression too: v1 returns
        # in MODULE_2_RUNNING, unlike the historical post-780 continuation.
        app = object.__new__(OperatorApp)
        app.controller = controller
        app.config = SimpleNamespace(drive_duration_s=None)
        app._restore_display = lambda **kwargs: None
        app._run_module(Stage.MODULE_2_PREPARE)
        result = controller._operator_components().runs[-1]
        if collision_end:
            assert result.actual_active_drive_s < 600
            assert result.actual_active_end_session_s - result.actual_active_start_session_s < 600
            assert not result.window_complete
            assert result.completion_reason == "HAZARD_COLLISION"
            assert result.collision_stop["wait_seconds"] == pytest.approx(120)
            assert bool(result.collision_stop["errors"]) is termination.endswith("cleanup_error")
            assert controller.session.state is Stage.ABORTED
            assert controller.session.abort_reason == "HAZARD_COLLISION"
            assert controller.session.result["completion_status"] == "ENDED_AFTER_HAZARD_COLLISION"
            assert controller.session.result_export.archive.is_file()
            with ZipFile(controller.session.result_export.archive) as archive:
                assert archive.testzip() is None
                name = next(name for name in archive.namelist() if name.endswith("session_manifest.json"))
                saved = json.loads(archive.read(name))
                m2 = saved["components"]["module_runs"][-1]
                assert m2["completion_reason"] == "HAZARD_COLLISION"
                assert m2["collision_stop"]["confirmation"] == "SPACE"
                assert not m2["window_complete"]
            assert not app.running
            if condition is Module2Condition.NOA_L2:
                assert result.deactivation_count == 0  # terminal stop is not a driver override
                assert result.automation_on_seconds < 600
        else:
            assert result.actual_active_drive_s == pytest.approx(780)
            assert result.window_complete
            assert controller.session.state is Stage.SESSION_COMPLETE
        assert controller._operator_components().hazard_loop is None
    else:
        with pytest.raises(RuntimeError):
            controller.execute_current_module()
        assert controller.session.closed is (termination == "user_exit")
    assert len(scenes) == 1 and scenes[0].closed
    assert preparations == [
        Stage.TRAINING.value,
        Stage.MODULE_1_RUNNING.value,
        Stage.MODULE_2_RUNNING.value,
    ]
    record = controller._operator_components().hazard_observation
    assert record["scenario_version"] == "M2_CLEAR_ROAD_WARNING_V2_20261020"
    assert record["exposure_policy"] == "CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD"
    if termination not in ("user_exit", "error", "exit_not_presented"):
        assert record["warning"]["collision_warning"] is True
        assert record["warning"]["audio"]["cue_id"] == "hazard-three-beeps-v1"
        assert record["warning_audio"]["attempts"] == 1
        assert record["warning_audio"]["closed"]
        if controller.session.result_export is not None:
            with ZipFile(controller.session.result_export.archive) as archive:
                assert archive.testzip() is None
                name = next(n for n in archive.namelist()
                            if n.endswith("collection/hazard_response_metrics.json"))
                metrics = json.loads(archive.read(name))
                assert metrics["warning"]["audio"] == record["warning"]["audio"]
    assert record["provenance"]["client"] == "INJECTED_TEST_CLIENT"
    assert record["cleanup"] == {"failures": [], "remaining_actor_ids": []}
    assert record["partial_data"] is (termination not in ("normal", "collision", "exit_not_presented"))
    if termination == "exit_not_presented":
        assert record["occurrence_result"] == "NOT_OCCURRED"
        assert record["scene_reason"] == "P5_M2_EXIT_COORDINATOR_UNAVAILABLE"
        assert record["exit_sequence_at_reveal"] is None
    else:
        assert record["exit_sequence_at_reveal"]["physical_outcome"] == "MISSED"
    if termination == "scene_failure":
        assert record["occurrence_result"] == "INVALID"
    if collision_end:
        assert record["driver_response_outcome"] == "COLLISION"
        assert record["execution_status"] == "CONTROLLED_COLLISION_STOP"
        assert record["end_reason"] == "HAZARD_COLLISION"
        assert record["collision_stop"]["confirmation"] == "SPACE"
        assert record["raw_storage"]["pending_sample_count"] == 0
    assert all(view.attach_count == view.close_count == 1 for view in views)
    assert all(world.vehicle.destroy_count == 1 for world in worlds)
    assert all(attestor.calls for attestor in attestations)
    assert (
        scenes[0].session.bundle.automation_runtime.state.control_mode.value == "MANUAL"
    )
    assert controller._done_claim().participant_ready is False
    if not controller.session.closed:
        controller.abort("TEST_OPERATOR_STOP")
