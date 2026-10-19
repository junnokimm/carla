import random
from types import SimpleNamespace

import carla
import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.p7.moving_config import MovingHazardConfig
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import P7Error
from src.p8.operator_m2_hazard import OperatorM2Hazard
from src.scenario.camera_feed import DisplayedCameraFrame
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from tests.m2_hazard_fakes import Actor, Waypoint, World, front_feed
from tests.research_noa_fakes import FakeMonotonicClock


def scene_fixture(tmp_path, seed=42, mode="MANUAL", scene_class=OperatorM2Hazard):
    clock = FakeMonotonicClock()
    world = World(monotonic_clock=clock)
    events, records, pair_calls = [], [], []
    source = SimpleNamespace(last_snapshot_sample=None)
    state = SimpleNamespace(control_mode=SimpleNamespace(value=mode))
    tm = SimpleNamespace(
        hazard_pair_revision=0,
        lane_change_in_flight=None,
        set_hazard_pair_detection=lambda cargo, enabled: pair_calls.append(
            (cargo.id, enabled)
        ),
    )
    session = SimpleNamespace(
        world=world,
        hero=world.vehicle,
        viewer=SimpleNamespace(feeds=[front_feed(world.vehicle)]),
        bundle=SimpleNamespace(
            traffic_manager_backend=tm, automation_runtime=SimpleNamespace(state=state)
        ),
        persistence=SimpleNamespace(source=source, flush_events=lambda: None),
        config=SimpleNamespace(run_id="run", traffic_manager_port=8000),
        exit_assistance=None,
        monotonic_clock=clock,
    )

    def stamp():
        return TimestampEnvelope(
            HostClockTimestamp(int(clock() * 1e9) + 1, int(clock() * 1e9) + 100)
        )

    services = SimpleNamespace(
        context=SimpleNamespace(participant_id="DEV-1", study_run_id="run"),
        stamp=stamp,
        emit=lambda kind, payload: events.append((kind, payload)),
    )
    scene = scene_class(
        session,
        services,
        MovingHazardConfig(seed),
        OccurrenceRegistry(tmp_path / "occ"),
        records.append,
        clock(),
        {"client": "INJECTED_TEST_CLIENT"},
    )
    return scene, world, clock, events, records, pair_calls


def tick(scene, world, dt, *, speed=12, y=None, display=True, driver=None):
    snapshot = world.step(dt, speed, y)
    t = snapshot.timestamp.elapsed_seconds
    host = HostClockTimestamp(int(t * 1e9) + 10, int(t * 1e9) + 1000)
    stamp = TimestampEnvelope(
        host,
        CarlaSnapshotTimestamp(
            t, snapshot.frame, host.monotonic_ns - 1, host.monotonic_ns
        ),
    )
    ego = world.vehicle
    wp = world.map.get_waypoint(ego.transform.location)
    scene.session.persistence.source.last_snapshot_sample = ResearchSnapshotSample(
        stamp,
        ego.transform,
        wp.road_id,
        wp.section_id,
        wp.lane_id,
        wp.lane_width,
        wp.transform,
        None,
        None,
        None,
        ego.get_control(),
        "off",
        speed * 3.6,
        world_snapshot=snapshot,
    )
    scene.after_control(
        driver or DriverInput(throttle=0.3), int(t * 1e9) - 20, int(t * 1e9) - 10
    )
    if display:
        frame = DisplayedCameraFrame(
            900,
            snapshot.frame,
            t,
            t + 100,
            int(t * 1e9) + 30,
            scene.session.viewer.feeds[0].sensor.get_transform(),
        )
        scene.after_display_completed(frame)


def prepare(scene, world):
    tick(scene, world, scene.planned - scene.config.preparation_s)
    assert scene.state == "OCCLUDED"


def test_seed_is_one_draw_and_does_not_touch_global_rng(tmp_path):
    before = random.getstate()
    a, world, _, _, _, _ = scene_fixture(tmp_path / "a")
    b, _, _, _, _, _ = scene_fixture(tmp_path / "b")
    assert 400 <= a.planned <= 600 and a.planned == b.planned
    for dt in [0, 100, 100, 100, 99.9]:
        tick(a, world, dt)
    assert a.planned == b.planned and random.getstate() == before
    assert len(world.actors) == 2  # Ego + collision observer; no scene actors.
    a.finalize("USER_EXIT")
    b.finalize("USER_EXIT")


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
def test_physical_backend_scene_finishes_but_raw_observation_continues_to_780(
    tmp_path, mode
):
    scene, world, clock, events, records, pair_calls = scene_fixture(
        tmp_path, mode=mode
    )
    prepare(scene, world)
    cargo_pose = scene.backend.cargo.get_transform()
    command_index = None
    for _ in range(220):
        tick(scene, world, 0.1, y=-3.5 if scene.first_geometry else 0)
        if scene.backend.motion_started is not None and command_index is None:
            command_index = world.frame
    assert scene.first_geometry is not None and scene.first_display is not None
    assert command_index < scene.first_geometry["frame"]
    assert 400 <= scene.first_geometry["m2_active_elapsed_s"] <= 600
    assert scene.first_geometry["ttc_candidate_s"] is not None
    assert (
        scene.backend.cargo.get_transform().location.distance(cargo_pose.location) == 0
    )
    assert scene.backend.lead.transform_commands == []
    assert scene.backend.lead.physics_commands == []
    assert scene.backend.lead.applied_controls
    assert (
        world.vehicle.applied_controls == [] and world.vehicle.transform_commands == []
    )
    assert scene.session.bundle.automation_runtime.state.control_mode.value == mode
    tick(scene, world, 780 - clock(), speed=0, driver=DriverInput(brake=0.7))
    scene.finalize("M2_END")
    actor_count = len(world.actors)
    scene.after_control(DriverInput(), 0, 0)
    scene.finalize("LATE_CALLBACK")
    assert len(world.actors) == actor_count and len(records) == 1
    assert records[0]["m2_active_elapsed_s"] == pytest.approx(780)
    assert records[0]["hazard_visible_t"] is None
    assert records[0]["driver_response_outcome"] is None
    raw = [
        row
        for name, payload in events
        if name == "m2_hazard_raw_samples"
        for row in payload["samples"]
    ]
    assert raw[-1]["input"]["brake"] == 0.7
    assert raw[-1]["poll_completed_monotonic_ns"] < raw[-1]["observation_monotonic_ns"]
    assert pair_calls[0][1] is False and pair_calls[-1][1] is True
    assert records[0]["cleanup"] == {"failures": [], "remaining_actor_ids": []}
    assert world.vehicle.destroy_count == 0 and not world.callbacks


@pytest.mark.parametrize("elapsed", [399.9, 400, 599.9, 600, 600.01, 780])
def test_window_frame_gaps_never_create_late_or_duplicate_opportunities(
    tmp_path, elapsed
):
    scene, world, _, events, records, _ = scene_fixture(tmp_path)
    scene.session.exit_assistance = SimpleNamespace(
        status=SimpleNamespace(value="PRESENTED"), lane_change_in_progress=True
    )
    tick(scene, world, elapsed)
    tick(scene, world, 0)
    assert not scene.claimed
    assert not any(name == "m2_hazard_lead_move_command" for name, _ in events)
    scene.finalize("M2_END")
    assert records[0]["first_geometry"] is None


@pytest.mark.parametrize("status", ["COMPLETED", "NOT_PRESENTED", "INTERRUPTED"])
def test_p5_terminal_path_can_prepare_from_current_connected_lane(tmp_path, status):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    scene.session.exit_assistance = SimpleNamespace(
        status=SimpleNamespace(value=status), lane_change_in_progress=False
    )
    prepare(scene, world)
    assert scene.backend.route.source[0].transform.location.x > 4000
    scene.finalize("USER_EXIT")


def test_stopped_ego_has_invalid_ttc_not_zero_and_keeps_raw_input(tmp_path):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    prepare(scene, world)
    tick(scene, world, 0.1, speed=0)
    assert scene.last_geometry["ttc_candidate_s"] is None
    assert scene.last_geometry["ttc_invalid_reason"]
    scene.finalize("USER_EXIT")


def test_actual_early_exposure_is_failure_not_valid_hazard(tmp_path):
    scene, world, _, _, records, _ = scene_fixture(tmp_path)
    scene.planned = 400.0
    scene.next_attempt = 394.0
    prepare(scene, world)
    world.actors[-2].transform = carla.Transform(
        carla.Location(x=world.actors[-2].transform.location.x, y=3.5)
    )
    with pytest.raises(P7Error, match="INVALID_EXPOSURE") as caught:
        tick(scene, world, 0.1)
    scene.finalize("ERROR", caught.value)
    assert records[0]["first_geometry"]["normal_window_candidate"] is False
    assert records[0]["first_geometry"]["m2_active_elapsed_s"] < 400


def test_spawn_failure_preserves_partial_cleanup_and_reservation(tmp_path):
    scene, world, _, events, records, _ = scene_fixture(tmp_path)
    world.spawn_failure = "static.prop.box01"
    with pytest.raises(P7Error, match="SPAWN_FAILED") as caught:
        prepare(scene, world)
    scene.finalize("ERROR", caught.value)
    assert records[0]["occurrence_reserved"]
    assert records[0]["partial_data"]
    assert any(name == "m2_hazard_preparation_failed" for name, _ in events)
    assert all(actor.destroy_count == 1 for actor in world.actors[1:])


def test_collision_keeps_sensor_original_frame_and_allows_module_end(tmp_path):
    scene, world, _, events, records, _ = scene_fixture(tmp_path)
    scene.backend._sensor.callback(
        SimpleNamespace(other_actor=SimpleNamespace(id=999), frame=3, timestamp=12.25)
    )
    tick(scene, world, 1)
    tick(scene, world, 779)
    scene.finalize("M2_END")
    failure = next(p for n, p in events if n == "m2_hazard_driving_event")
    assert failure["collisions"][0]["frame"] == 3
    assert failure["collisions"][0]["simulation_seconds"] == 12.25
    assert not records[0]["partial_data"]
    assert records[0]["driving_event_counts"]["COLLISION"] == 1


def test_wrong_frame_display_cannot_borrow_latest_geometry(tmp_path):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    prepare(scene, world)
    frame = DisplayedCameraFrame(
        900,
        99999,
        999.0,
        999.0,
        123,
        scene.session.viewer.feeds[0].sensor.get_transform(),
    )
    assert scene.backend.display_geometry(frame) is None
    scene.after_display_completed(frame)
    assert scene.first_display is None
    scene.finalize("USER_EXIT")


@pytest.mark.parametrize("planned", [400.0, 600.0])
def test_window_endpoints_schedule_preparation_and_command_ahead_of_exposure(
    tmp_path, planned
):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    scene.planned, scene.next_attempt = planned, planned - 6
    prepare(scene, world)
    assert scene.command_at < planned
    for _ in range(61):
        try:
            tick(scene, world, 0.1)
        except P7Error as error:
            # A physical/model deviation across the boundary is invalid, not
            # relabelled a valid random event. Preserve the observed candidate.
            assert "INVALID_EXPOSURE" in str(error)
            assert not scene.first_geometry["normal_window_candidate"]
            break
    commands = [p for n, p in events if n == "m2_hazard_lead_move_command"]
    assert len(commands) == 1 and commands[0]["m2_active_elapsed_s"] <= 600
    scene.finalize("USER_EXIT")


def test_surface_clearance_ttc_and_off_path_invalidity(tmp_path):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    prepare(scene, world)
    tick(scene, world, 0.1)  # Confirm the placement on a later world frame.
    # Ego front x+2.3, fixed cargo approaching face x-0.4.
    cargo_x = scene.backend.cargo.get_transform().location.x
    ego = world.vehicle
    ego.transform = carla.Transform(carla.Location(x=cargo_x - 50))
    ego.velocity = carla.Vector3D(x=10)
    geometry = scene.backend.geometry(world.get_snapshot())
    assert geometry["clearance_m"] == pytest.approx(47.3, abs=0.001)
    assert geometry["ttc_candidate_s"] == pytest.approx(4.73, abs=0.001)
    ego.transform = carla.Transform(carla.Location(x=cargo_x - 50, y=3.5))
    assert scene.backend.geometry(world.get_snapshot())["ttc_candidate_s"] is None
    scene.finalize("USER_EXIT")


def test_lane_failure_defers_same_opportunity_and_later_succeeds(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    original = world.map.get_waypoint
    world.map.get_waypoint = lambda *args, **kwargs: None
    # Build the source snapshot using the actual source map, then selectively
    # reject backend planning, as a disconnected lane would do.
    scene.backend.world_map = SimpleNamespace(get_waypoint=world.map.get_waypoint)
    world.map.get_waypoint = original
    tick(scene, world, scene.next_attempt)
    assert scene.state == "SCHEDULED" and not scene.claimed
    assert "LANE_UNSUITABLE" in scene.last_reason
    planned = scene.planned
    scene.backend.world_map = world.map
    tick(scene, world, 1)
    assert scene.state == "OCCLUDED" and scene.planned == planned
    assert any(n == "m2_hazard_preparation_deferred" for n, _ in events)
    scene.finalize("USER_EXIT")


@pytest.mark.parametrize("defect", ["fork", "curve", "opposite"])
def test_disconnected_or_opposite_or_curved_routes_are_not_spawned(tmp_path, defect):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)

    def waypoint(location, **kwargs):
        wp = Waypoint(location.x)
        if defect == "fork":
            wp.next = lambda distance: [
                Waypoint(location.x + distance),
                Waypoint(location.x + distance, -2),
            ]
        elif defect == "curve":
            nxt = Waypoint(location.x + 4)
            nxt.transform = carla.Transform(
                nxt.transform.location, carla.Rotation(yaw=30)
            )
            wp.next = lambda distance: [nxt]
        else:
            wp.get_left_lane = lambda: Waypoint(location.x, 1)
            wp.get_right_lane = lambda: None
        return wp

    scene.backend.world_map = SimpleNamespace(get_waypoint=waypoint)
    tick(scene, world, scene.next_attempt)
    assert scene.state == "SCHEDULED" and not scene.claimed
    assert scene.last_reason.startswith("LANE_UNSUITABLE")
    scene.finalize("USER_EXIT")


def test_occupied_corridor_defers_but_distant_vehicle_does_not_block(tmp_path):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    tick(scene, world, scene.next_attempt - 1)
    other = Actor(
        777,
        pose=carla.Transform(carla.Location(x=world.vehicle.transform.location.x + 30)),
    )
    world.actors.append(other)
    tick(scene, world, 1)
    assert scene.last_reason == "SPAWN_CORRIDOR_OCCUPIED" and not scene.claimed
    other.transform = carla.Transform(carla.Location(x=-1000))
    tick(scene, world, 1)
    assert scene.state == "OCCLUDED"
    scene.finalize("USER_EXIT")
    assert other.destroy_count == 0


def test_scoped_tm_pair_is_reasserted_on_user_reactivation_and_restored():
    from src.scenario.research_noa_traffic_manager import (
        TrafficManagerNoAControlBackend,
    )
    from tests.m2_hazard_fakes import TM

    ego, cargo, tm = Actor(), Actor(123, "static.prop.box01"), TM()
    backend = TrafficManagerNoAControlBackend(ego, tm)
    backend.set_hazard_pair_detection(cargo, False)
    initial_modes = list(ego.autopilot_port_calls)
    assert initial_modes == [(False, 8000)]  # Pair setting never activates NoA.
    backend.enter_noa_control()  # Represents existing driver's explicit activation.
    backend.enter_manual_control()
    backend.set_hazard_pair_detection(cargo, True)
    calls = [call for call in tm.calls if call[0] == "collision_detection"]
    assert calls == [
        ("collision_detection", 42, 123, False),
        ("collision_detection", 42, 123, False),
        ("collision_detection", 42, 123, True),
    ]
    assert ego.autopilot_port_calls[-1] == (False, 8000)


def test_cleanup_failure_is_failed_partial_record_and_does_not_delete_ego(tmp_path):
    scene, world, _, _, records, _ = scene_fixture(tmp_path)
    prepare(scene, world)
    scene.backend.lead.destroy = lambda: False
    with pytest.raises(P7Error, match="CLEANUP_INCOMPLETE"):
        scene.finalize("M2_END")
    assert records[0]["execution_status"] == "FAILED"
    assert records[0]["partial_data"]
    assert records[0]["cleanup"]["remaining_actor_ids"] == [scene.backend.lead.id]
    assert world.vehicle.destroy_count == 0


def test_collision_after_last_frame_is_retained_at_shutdown(tmp_path):
    scene, world, _, _, records, _ = scene_fixture(tmp_path)
    scene.backend._sensor.callback(
        SimpleNamespace(other_actor=SimpleNamespace(id=777), frame=10, timestamp=780.0)
    )
    scene.finalize("M2_END")
    assert records[0]["execution_status"] == "DEVELOPMENT_RUN_ENDED"
    assert records[0]["driving_event_counts"]["COLLISION"] == 1
    assert not records[0]["partial_data"]
    assert records[0]["cleanup"]["pending_collisions"][0]["frame"] == 10
    assert world.vehicle.destroy_count == 0
