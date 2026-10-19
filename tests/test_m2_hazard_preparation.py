"""Preparation retries on connected curved roads; no CARLA client is opened."""

from math import atan2, cos, degrees, hypot, radians, sin
from types import SimpleNamespace

import carla
import pytest

from src.p7.moving_config import TARGET_TTC_S, WINDOW
from src.p7.moving_route import MovingRoute, box_record
from src.p7.types import P7Error
from tests.m2_hazard_fakes import Map, Waypoint
from tests.test_m2_hazard_v1 import scene_fixture, tick


class CircularWaypoint(Waypoint):
    def __init__(self, station, radius, lane=-3):
        self.station, self.radius, self.lane_id = station, radius, lane
        offset = (lane + 3) * self.lane_width
        angle = station / radius
        self.transform = carla.Transform(
            carla.Location(
                x=(radius - offset) * sin(angle),
                y=radius - (radius - offset) * cos(angle),
            ),
            carla.Rotation(yaw=degrees(angle)),
        )

    def next(self, distance):
        # next(distance) follows this lane's own arc length. Adjacent lanes
        # therefore have physically different radii, not copied straight points.
        offset = (self.lane_id + 3) * self.lane_width
        return [
            CircularWaypoint(
                self.station + distance * self.radius / (self.radius - offset),
                self.radius,
                self.lane_id,
            )
        ]

    def get_left_lane(self):
        return (
            CircularWaypoint(self.station, self.radius, self.lane_id + 1)
            if self.lane_id < -1
            else None
        )

    def get_right_lane(self):
        return CircularWaypoint(self.station, self.radius, self.lane_id - 1)


class CircularMap(Map):
    def __init__(self, radius):
        self.radius = radius

    def get_waypoint(self, location, project_to_road=True, lane_type=None):
        angle = atan2(location.x / self.radius, 1 - location.y / self.radius)
        offset = self.radius - hypot(location.x, self.radius - location.y)
        lane = -3 + round(offset / 3.5)
        waypoint = CircularWaypoint(angle * self.radius, self.radius, lane)
        if (
            not project_to_road
            and waypoint.transform.location.distance(location) > 1.75
        ):
            return None
        return waypoint


def circular_scene(tmp_path, radius):
    scene, world, clock, events, records, pair_calls = scene_fixture(tmp_path)
    world.map = CircularMap(radius)
    scene.backend.world_map = world.map
    station = lateral = 0.0

    def step(dt, ego_speed=12, ego_y=None):
        nonlocal station, lateral
        world.simulation_seconds += dt
        world.frame += 1
        clock.advance(dt)
        station += ego_speed * dt
        if ego_y is not None:
            lateral = ego_y
        angle = station / radius
        world.vehicle.transform = carla.Transform(
            carla.Location(
                x=(radius - lateral) * sin(angle),
                y=radius - (radius - lateral) * cos(angle),
            ),
            carla.Rotation(yaw=degrees(angle)),
        )
        world.vehicle.velocity = carla.Vector3D(
            x=ego_speed * cos(angle), y=ego_speed * sin(angle)
        )
        for actor in world.actors[1:]:
            if actor.is_alive and actor.type_id.startswith("vehicle"):
                actor.physics(dt)
        snapshot = world.get_snapshot()
        for callback in world.callbacks.values():
            callback(snapshot)
        return snapshot

    world.step = step
    return scene, world, clock, events, records, pair_calls


def payloads(events, name):
    return [payload for kind, payload in events if kind == f"m2_hazard_{name}"]


def test_curve_preview_defers_before_claim_then_uses_current_shorter_preparation(
    tmp_path,
):
    scene, world, _, events, _, _ = circular_scene(tmp_path, radius=1000)
    original_bounds = box_record(world.vehicle.bounding_box)
    planned = scene.planned
    try:
        tick(scene, world, scene.next_attempt)

        assert scene.state == "SCHEDULED" and not scene.claimed
        assert scene.last_reason == "PREPARATION_OCCLUSION_UNSUITABLE"
        assert scene.backend.lead is None and scene.backend.cargo is None
        assert len(world.actors) == 2  # Ego and the existing collision observer.
        first_attempt = payloads(events, "preparation_deferred")[0]
        assert first_attempt["preparation_s"] == pytest.approx(6)
        assert first_attempt["route_attempt"]["occlusion_failure_preview_s"] >= 0
        first_origin = first_attempt["route_attempt"]["ego_pose"]

        for _ in range(3):
            tick(scene, world, 1)
            if scene.state == "OCCLUDED":
                break

        assert scene.state == "OCCLUDED" and scene.claimed
        assert 3 <= scene.preparation_s < 6
        assert scene.backend.preparation_s == scene.preparation_s
        assert scene.planned == planned
        assert len(payloads(events, "preparation_started")) == 1
        assert len(payloads(events, "actor_spawn")) == 2
        plan = payloads(events, "exposure_plan")[0]
        assert plan["preparation_s"] == scene.preparation_s
        assert plan["adjusted_target_active_s"] == pytest.approx(planned)
        assert plan["command_target_active_s"] == pytest.approx(
            planned - plan["predicted_partial_exposure_delay_s"]
        )
        current_origin = scene.backend.preparation_attempt["ego_pose"]
        assert (
            hypot(
                current_origin["x"] - first_origin["x"],
                current_origin["y"] - first_origin["y"],
            )
            >= 11.9
        )
        assert box_record(world.vehicle.bounding_box) == original_bounds
        assert not world.vehicle.applied_controls
        assert not world.vehicle.transform_commands
        assert not world.vehicle.autopilot_port_calls

        for _ in range(600):
            tick(scene, world, 1 / 60)
            if scene._lead_complete:
                break
            assert not scene.backend.lead_handed_to_tm
        assert scene._lead_complete
        assert scene.first_geometry is not None and scene.first_display is not None
        first = scene.first_geometry
        assert first["normal_window_candidate"]
        assert WINDOW[0] <= first["m2_active_elapsed_s"] <= WINDOW[1]
        assert first["ttc_candidate_s"] > 0
        assert first["ttc_candidate_s"] == pytest.approx(
            first["clearance_m"] / first["closing_speed_mps"]
        )
        assert (
            payloads(events, "lead_move_command")[0]["observation"]["frame"]
            < (first["frame"])
        )
        assert len(payloads(events, "geometric_exposure")) == 1
        assert box_record(world.vehicle.bounding_box) == original_bounds
        assert not world.vehicle.applied_controls
        assert not world.vehicle.transform_commands
    finally:
        scene.finalize("USER_EXIT")


def test_curve_without_occluded_candidate_keeps_observation_until_780(tmp_path):
    scene, world, clock, events, records, _ = circular_scene(tmp_path, radius=500)
    planned = scene.planned
    try:
        tick(scene, world, scene.next_attempt)
        for _ in range(4):
            tick(scene, world, 1)
        assert scene.state == "SCHEDULED" and not scene.claimed
        deferred = payloads(events, "preparation_deferred")
        assert [event["preparation_s"] for event in deferred] == pytest.approx(
            [6, 5, 4, 3, 3]
        )
        assert all(
            event["reason"] == "PREPARATION_OCCLUSION_UNSUITABLE"
            and event["route_attempt"]["preparation_s"] == event["preparation_s"]
            for event in deferred
        )
        tick(scene, world, 600.01 - clock())
        assert scene.state == "NOT_OCCURRED" and not scene.claimed
        tick(scene, world, 780 - clock(), speed=0)
        scene.finalize("M2_END")
    finally:
        if not scene.closed:
            scene.finalize("USER_EXIT")
    assert scene.planned == planned
    assert len(world.actors) == 2
    assert not payloads(events, "actor_spawn")
    assert not payloads(events, "lead_move_command")
    assert records[0]["m2_active_elapsed_s"] == pytest.approx(780)
    assert records[0]["hazard_state"] == "NOT_OCCURRED"
    assert records[0]["occurrence_reserved"] is False
    assert records[0]["first_geometry"] is None
    raw = [
        sample
        for payload in payloads(events, "raw_samples")
        for sample in payload["samples"]
    ]
    assert raw[-1]["m2_active_elapsed_s"] == pytest.approx(780)
    assert world.vehicle.destroy_count == 0


def test_minimum_preparation_cannot_spill_past_exposure_window(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    scene.planned, scene.next_attempt = 600.0, 594.0
    preflight = scene.backend.preflight

    def defer_until_too_late(sample, preparation_s=None):
        if scene.elapsed < 598:
            raise P7Error("PREPARATION_OCCLUSION_UNSUITABLE")
        return preflight(sample, preparation_s)

    scene.backend.preflight = defer_until_too_late
    try:
        tick(scene, world, 594)
        for _ in range(4):
            tick(scene, world, 1)
        assert scene.state == "NOT_OCCURRED" and not scene.claimed
        assert scene.last_reason == "INSUFFICIENT_PREPARATION_TIME_IN_WINDOW"
        assert len(world.actors) == 2
        assert not payloads(events, "actor_spawn")
        assert not payloads(events, "lead_move_command")
        assert scene.planned == 600.0
    finally:
        scene.finalize("USER_EXIT")


def test_short_preparation_keeps_nominal_ttc_placement_and_lead_route_lifetime(
    tmp_path,
):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    scene.planned, scene.next_attempt = 430.0, 424.0
    preflight = scene.backend.preflight

    def defer_first_three_attempts(sample, preparation_s=None):
        if scene.elapsed < 427:
            raise P7Error("PREPARATION_OCCLUSION_UNSUITABLE")
        return preflight(sample, preparation_s)

    scene.backend.preflight = defer_first_three_attempts
    try:
        tick(scene, world, 424)
        for _ in range(3):
            tick(scene, world, 1)
        assert scene.state == "OCCLUDED"
        assert scene.preparation_s == scene.backend.preparation_s == 3
        assert TARGET_TTC_S == 4.0
        box = world.vehicle.bounding_box
        assert scene.backend.cargo_station == pytest.approx(
            12 * (3 + TARGET_TTC_S) + box.extent.x + box.location.x + 1.0
        )
        plan = payloads(events, "exposure_plan")[0]
        assert plan["adjusted_target_active_s"] == 430.0
        assert plan["preparation_s"] == 3.0

        for _ in range(600):
            tick(scene, world, 1 / 60)
            if scene._lead_complete:
                break
            assert not scene.backend.lead_handed_to_tm

        assert scene._lead_complete
        assert scene.first_geometry is not None and scene.first_display is not None
        first = scene.first_geometry
        assert WINDOW[0] <= first["m2_active_elapsed_s"] <= WINDOW[1]
        assert first["ttc_candidate_s"] > 0
        assert first["ttc_candidate_s"] == pytest.approx(
            first["clearance_m"] / first["closing_speed_mps"]
        )
        assert not world.vehicle.applied_controls
        assert not world.vehicle.transform_commands
    finally:
        scene.finalize("USER_EXIT")


def test_preview_refuses_unmeasured_lead_asset_before_claim_or_spawn(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    world.vehicle.type_id = "vehicle.other_asset"
    try:
        tick(scene, world, scene.next_attempt)
        assert scene.state == "SCHEDULED" and not scene.claimed
        assert scene.last_reason == "LEAD_PREVIEW_BOUNDS_UNAVAILABLE"
        assert scene.backend.lead is None and scene.backend.cargo is None
        assert len(world.actors) == 2
        assert not payloads(events, "actor_spawn")
    finally:
        scene.finalize("USER_EXIT")


def test_actual_prepared_and_displayed_geometry_preserves_asset_bounds(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    ego_bounds = box_record(world.vehicle.bounding_box)
    try:
        tick(scene, world, scene.next_attempt)
        assert scene.state == "OCCLUDED" and scene.preparation_s == 6
        lead, cargo = scene.backend.lead, scene.backend.cargo
        actor_bounds = {
            actor.id: box_record(actor.bounding_box) for actor in (lead, cargo)
        }
        tick(scene, world, 0.1)
        assert scene.display_frames_checked > 0
        assert box_record(world.vehicle.bounding_box) == ego_bounds
        assert box_record(lead.bounding_box) == ego_bounds
        assert {
            actor.id: box_record(actor.bounding_box) for actor in (lead, cargo)
        } == actor_bounds
        assert not lead.transform_commands and not lead.physics_commands
        assert cargo.physics_commands == [False]
        assert len(cargo.transform_commands) == 1
        assert not world.vehicle.applied_controls
        assert not world.vehicle.transform_commands
        assert payloads(events, "actors_prepared")[0]["initial_occlusion_proxy"]
    finally:
        scene.finalize("USER_EXIT")


def test_p5_deferral_does_not_relabel_an_older_route_preview_as_current(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    scene.backend.world_map = SimpleNamespace(get_waypoint=lambda *a, **kw: None)
    try:
        tick(scene, world, scene.next_attempt)
        assert scene.state == "SCHEDULED" and not scene.claimed
        previous = payloads(events, "preparation_deferred")[0]
        assert previous["route_attempt"] is not None
        assert previous["route_attempt"]["preparation_s"] == 6

        scene.session.exit_assistance = SimpleNamespace(
            status=SimpleNamespace(value="PRESENTED"), lane_change_in_progress=True
        )

        def unexpected_preflight(*args, **kwargs):
            raise AssertionError("A P5 blocker must prevent a new route attempt")

        scene.backend.preflight = unexpected_preflight
        tick(scene, world, 1)
        current = payloads(events, "preparation_deferred")[-1]
        assert current["reason"] == "P5_CONFLICT:PRESENTED"
        assert current["preparation_s"] == 5
        assert current["route_attempt"] is None
        assert previous["route_attempt"]["preparation_s"] == 6
        assert not scene.claimed and len(world.actors) == 2
    finally:
        scene.finalize("USER_EXIT")


def test_graded_route_ttc_uses_full_three_dimensional_closing_velocity(tmp_path):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    angle = radians(10)

    def pose(station, lateral=0):
        return carla.Transform(
            carla.Location(x=station * cos(angle), y=lateral, z=station * sin(angle)),
            carla.Rotation(pitch=degrees(angle)),
        )

    try:
        tick(scene, world, scene.next_attempt)
        assert scene.state == "OCCLUDED"
        tick(scene, world, 0.1)  # Confirm the placement on a later world frame.
        source, target = [], []
        for station in (0.0, 120.0):
            a, b = Waypoint(0), Waypoint(0, -2)
            a.transform, b.transform = pose(station), pose(station, 3.5)
            source.append(a)
            target.append(b)
        scene.backend.route = MovingRoute(source, target, [0.0, 120.0], "left")

        # Configure synthetic snapshot actors directly, as World.step does. No
        # production ego control/teleport API is used by the hazard backend.
        world.vehicle.transform = pose(10)
        world.vehicle.velocity = carla.Vector3D(x=10 * cos(angle), z=10 * sin(angle))
        scene.backend.lead.transform = pose(30)
        scene.backend.cargo.transform = pose(60)
        scene.backend.cargo.transform.location.z += 0.42
        scene.backend.cargo_pose = scene.backend.cargo.get_transform()
        geometry = scene.backend.geometry(world.get_snapshot())

        assert geometry["collision_path_proxy"]
        assert geometry["closing_speed_mps"] == pytest.approx(10, abs=1e-5)
        assert geometry["ttc_candidate_s"] == pytest.approx(
            geometry["clearance_m"] / 10, abs=1e-5
        )
        assert not world.vehicle.applied_controls
        assert not world.vehicle.transform_commands
    finally:
        scene.finalize("USER_EXIT")


@pytest.mark.parametrize("preparation_s", [3.0, 6.0])
@pytest.mark.parametrize("grade_degrees", [-10.0, 10.0])
def test_exposure_prediction_follows_graded_route_height(
    tmp_path, preparation_s, grade_degrees
):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    try:
        tick(scene, world, scene.next_attempt)
        assert scene.state == "OCCLUDED"
        backend = scene.backend
        backend.preparation_s = preparation_s
        predictions = []
        for grade in (0.0, grade_degrees):
            rotation = carla.Transform(rotation=carla.Rotation(pitch=grade))

            def pose(station, lateral=0, height=0, *, basis=rotation, pitch=grade):
                return carla.Transform(
                    basis.transform(carla.Location(x=station, y=lateral, z=height)),
                    carla.Rotation(pitch=pitch),
                )

            source, target = [], []
            for station in (0.0, 200.0):
                a, b = Waypoint(0), Waypoint(0, -2)
                a.transform, b.transform = pose(station), pose(station, 3.5)
                source.append(a)
                target.append(b)
            backend.route = MovingRoute(source, target, [0.0, 200.0], "left")
            backend.lead.transform = pose(18, height=0.3)
            backend.cargo_pose = pose(
                12 * (preparation_s + TARGET_TTC_S) + 3.3, height=0.42
            )
            sample = SimpleNamespace(transform=pose(0, height=0.1), speed_kmh=12 * 3.6)
            predictions.append(backend.predict_first_exposure(sample))

        # A rigid rotation of the road/camera/actor arrangement must not turn a
        # normally hidden cargo into immediate predicted exposure. Allow one
        # existing 60-sample prediction interval for the world-vertical ground
        # offsets and CARLA's single-precision transforms.
        resolution = scene.config.lane_change_s / 60
        assert resolution < predictions[0] < scene.config.lane_change_s
        assert predictions[1] == pytest.approx(predictions[0], abs=resolution)
        assert not world.vehicle.applied_controls
        assert not world.vehicle.transform_commands
    finally:
        scene.finalize("USER_EXIT")
