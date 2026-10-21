import carla
import pygame
import pytest
from dataclasses import replace

from src.p8.clear_road_hazard import ClearRoadM2Hazard
from tests.m2_hazard_fakes import Actor
from tests.test_m2_hazard_v1 import scene_fixture, tick
from src.experiment.context import ExperimentModule, Module2Condition
from tests.test_p5_exit_assistance import ACTIVE, MANUAL, observation, stamp
from tests.test_p8_signal_exit import guided


def setup(tmp_path, mode="NOA_ACTIVE"):
    scene, world, clock, events, records, pairs = scene_fixture(
        tmp_path, mode=mode, scene_class=ClearRoadM2Hazard)
    world.vehicle.speed_limit_kmh = 43.2
    # These scene tests start after a genuinely presented M2 exit opportunity.
    # Ordering tests replace this coordinator to cover missing/ongoing exits.
    subject, _ = guided(
        ExperimentModule.MODULE_2,
        Module2Condition.MANUAL if mode == "MANUAL" else Module2Condition.NOA_L2,
        mode=MANUAL if mode == "MANUAL" else ACTIVE,
    )
    subject._t0 = stamp(100_000_000_000)
    subject.observe(replace(observation(1400, lane=2, road=33, frame=2),
                            timestamp=stamp(200_000_000_000)))
    scene.session.exit_assistance = subject
    return scene, world, clock, events, records, pairs


def expose(scene, world):
    tick(scene, world, scene.planned - scene.config.preparation_s)
    for _ in range(90):
        tick(scene, world, .1)
        if scene.first_display:
            break
    assert scene.scene_failure is None, scene.last_reason
    assert scene.first_display


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
@pytest.mark.parametrize("lane_y", [0, 3.5, 7.0])
def test_clear_road_reveal_same_speed_current_lane_warning_and_no_ego_writes(tmp_path, mode, lane_y):
    scene, world, clock, events, records, pairs = setup(tmp_path, mode)
    world.vehicle.transform.location.y = lane_y
    expose(scene, world)
    geometry = scene.first_geometry
    assert scene.backend.lead is None
    assert [a for a in world.actors if a.type_id.startswith("vehicle")] == [world.vehicle]
    assert geometry["ego_speed_mps"] * 3.6 == pytest.approx(43.2)
    assert geometry["cargo_pose"]["y"] == pytest.approx(lane_y)
    assert 3.7 <= geometry["ttc_candidate_s"] <= 4.1
    assert 400 <= geometry["m2_active_elapsed_s"] <= 600
    assert pairs == [(scene.backend.cargo.id, False)]
    pygame.font.init()
    scene.draw_warning(pygame.Surface((1280, 720)))
    scene.after_warning_flip(1234, 5678)
    assert scene.warning_record["takeover_request"] is (mode == "NOA_ACTIVE")
    assert scene.warning_record["collision_warning"]
    assert not scene.warning_record["changes_control_authority"]
    assert scene.session.bundle.automation_runtime.state.control_mode.value == mode
    assert world.vehicle.applied_controls == world.vehicle.transform_commands == []
    assert world.vehicle.physics_commands == []
    for _ in range(140):
        tick(scene, world, .1, y=lane_y - 3.5)
    assert scene.state == "SCENE_RETIRED", scene.last_reason
    tick(scene, world, 780 - clock(), speed=0)
    scene.finalize("M2_END")
    assert records[-1]["m2_active_elapsed_s"] == pytest.approx(780)
    assert records[-1]["warning"]["flip_completed_perf_counter_ns"] == 5678
    assert records[-1]["scenario_version"] == scene.scenario_version
    assert pairs[-1] == (scene.backend.cargo.id, True)


@pytest.mark.parametrize("block", ["low_speed", "lead", "p5"])
def test_unready_scene_does_not_expose_or_stop_module(tmp_path, block):
    scene, world, clock, events, records, _ = setup(tmp_path)
    tick(scene, world, scene.planned - 7, speed=2.23 if block == "low_speed" else 12)
    if block == "p5":
        scene.session.bundle.traffic_manager_backend.lane_change_in_flight = "normal signal lane change"
    other = Actor(999)
    if block == "lead":
        world.actors.append(other)
    for i in range(100):
        other.transform = carla.Transform(carla.Location(x=world.vehicle.transform.location.x + 25))
        tick(scene, world, .1, speed=2.23 if block == "low_speed" else 12)
    assert scene.first_display is None and scene.backend.motion_started is None
    tick(scene, world, 601 - clock())
    assert scene.state in ("SCHEDULED", "READY_HIDDEN") and scene.scene_failure is None
    tick(scene, world, 780 - clock())
    assert scene.should_extend_drive(clock(), 780)
    scene.finalize("USER_EXIT")
    assert records[-1]["occurrence_result"] == "NOT_OCCURRED"
    assert world.vehicle.applied_controls == []


@pytest.mark.parametrize("fault", ["spawn", "reveal", "placement", "geometry"])
def test_hazard_failures_contained_and_raw_input_keeps_running(tmp_path, monkeypatch, fault):
    scene, world, clock, events, records, _ = setup(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")
    if fault == "spawn":
        world.spawn_failure = scene.config.cargo_blueprint
    if fault == "reveal":
        monkeypatch.setattr(scene.backend, "reveal", fail)
    if fault == "placement":
        monkeypatch.setattr(scene.backend, "_confirm_cargo_placement", lambda *a: False)
    if fault == "geometry":
        monkeypatch.setattr(scene.backend, "geometry", fail)
    tick(scene, world, scene.planned - 6)
    for _ in range(100):
        tick(scene, world, .1)
    assert scene.scene_failure is not None
    tick(scene, world, 780 - clock())
    scene.finalize("M2_END")
    assert records[-1]["occurrence_result"] == "INVALID"
    assert records[-1]["session_action"] == "CONTINUE"
    assert world.vehicle.is_alive and world.vehicle.applied_controls == []


def test_warning_render_or_log_failure_does_not_change_control(tmp_path, monkeypatch):
    scene, world, _, _, _, _ = setup(tmp_path)
    expose(scene, world)
    def fail(*args, **kwargs):
        raise RuntimeError("injected warning error")
    monkeypatch.setattr(pygame.font, "SysFont", fail)
    scene.draw_warning(pygame.Surface((1280, 720)))
    assert scene.warning_errors and not scene.warning_drawn
    assert scene.scene_failure is None and world.vehicle.applied_controls == []
    scene.warning_drawn = True
    monkeypatch.setattr(scene.services, "emit", fail)
    scene.after_warning_flip(123, 456)
    assert scene.continuation_errors
    assert scene.scene_failure is None and world.vehicle.applied_controls == []
    scene.finalize("M2_END")


def test_distant_traffic_does_not_block_clear_corridor(tmp_path):
    scene, world, _, _, _, _ = setup(tmp_path)
    world.actors.extend([Actor(998, pose=carla.Transform(carla.Location(x=-2000))),
                         Actor(999, pose=carla.Transform(carla.Location(x=20000)))])
    expose(scene, world)
    scene.finalize("M2_END")


@pytest.mark.parametrize("gap", [.4, 5.0])
def test_observation_gap_resets_speed_stability(tmp_path, gap):
    scene, world, _, _, _, _ = setup(tmp_path)
    tick(scene, world, scene.planned - 6)
    for _ in range(10):
        tick(scene, world, .1)
    tick(scene, world, gap)
    assert scene.stable_since == scene.elapsed
    assert scene.backend.motion_started is None
    scene.finalize("M2_END")


def test_late_ready_remains_eligible_after_original_window(tmp_path):
    scene, world, clock, _, records, _ = setup(tmp_path)
    tick(scene, world, 599)
    for _ in range(30):
        tick(scene, world, .1)
    assert scene.first_display and scene.first_display["normal_window_candidate"]
    assert scene.first_geometry["m2_active_elapsed_s"] > 600
    scene.finalize("M2_END")
    assert records[-1]["occurrence_result"] == "EXPOSURE_CANDIDATE"


@pytest.mark.parametrize("issue", [None, "no_warning", "invalid_scene", "auto_brake"])
@pytest.mark.parametrize("policy", ["CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD", ClearRoadM2Hazard.exposure_policy])
def test_new_condition_failures_do_not_become_analysis_ready_reaction_times(issue, policy):
    from src.p8.collection_hazard import hazard_metrics
    from tests.test_p8_collection import hazard_events
    events = hazard_events()
    if issue != "no_warning":
        events.append(("m2_hazard_warning_displayed", 10.05,
                       {"collision_warning": True, "takeover_request": True,
                        "flip_completed_monotonic_ns": 10_050_000_000}))
    events.append(("m2_hazard_end", 15, {
        "exposure_policy": policy,
        "occurrence_result": "INVALID" if issue == "invalid_scene" else "EXPOSURE_CANDIDATE"}))
    if issue == "auto_brake":
        events[1][2]["samples"][5].update(auto_state="NOA_ACTIVE",
                                          applied_control_readback={"brake": .2})
    report = hazard_metrics(events, 0)
    assert report["reaction_time_s"] == pytest.approx(1.0)
    assert report["experimental_condition_eligible"] is (issue is None)
    assert report["analysis_reaction_time_s"] == (1.0 if issue is None else None)


def test_display_speed_validation_does_not_change_noa_state(tmp_path):
    scene, world, _, _, records, _ = setup(tmp_path)
    expose(scene, world)
    assert scene.display_exposure_valid({**scene.first_display["geometry"], "ego_speed_mps": 8 / 3.6})
    assert not scene.display_exposure_valid({**scene.first_display["geometry"], "collision_path_proxy": False})
    assert scene.session.bundle.automation_runtime.state.control_mode.value == "NOA_ACTIVE"
    scene.finalize("M2_END")
