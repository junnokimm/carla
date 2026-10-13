from __future__ import annotations

import queue
from dataclasses import dataclass
from math import cos, sin

import carla

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.lane_geometry import compute_lane_relative_geometry
from src.experiment.timestamp import TimestampEnvelope
from src.p7.carla_actors import CarlaHazardActors
from src.p7.carla_route import planar
from src.p7.carla_traffic import in_hazard_zone
from src.p7.carla_visibility import camera_transform, projected_bounds
from src.p7.ownership import ActorRole
from src.p7.types import HazardObservation, P7Error


@dataclass(frozen=True, slots=True)
class CollisionRecord:
    other_id: int
    frame: int
    simulation_s: float


class CollisionObserver:
    def __init__(self, actors: CarlaHazardActors) -> None:
        blueprint = actors.world.get_blueprint_library().find("sensor.other.collision")
        self.sensor = actors.world.spawn_actor(blueprint, carla.Transform(), attach_to=actors.ego)
        actors.owned.add(self.sensor, ActorRole.SENSOR)
        self.records: queue.Queue[CollisionRecord] = queue.Queue(256)
        self.overflow = False
        self.sensor.listen(self._capture)

    def _capture(self, event: carla.CollisionEvent) -> None:
        try:
            self.records.put_nowait(CollisionRecord(event.other_actor.id, event.frame, event.timestamp))
        except queue.Full:
            self.overflow = True

    def take(self) -> CollisionRecord | None:
        if self.overflow:
            raise P7Error("COLLISION_OBSERVATION_OVERFLOW")
        try:
            return self.records.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self.sensor.stop()


def observe(actors: CarlaHazardActors, timestamp: TimestampEnvelope, *, driver: DriverInput | None,
            collision: CollisionRecord | None, foreign: tuple[carla.Actor, ...]) -> HazardObservation:
    ego_pose = actors.ego.get_transform()
    ego_s = actors.corridor.coordinates(ego_pose)[0]
    lead, cargo = actors.lead, actors.cargo
    alive = lead is not None and cargo is not None and lead.is_alive and cargo.is_alive
    lead_s, lead_lateral, clear, complete = actors.config.lead_initial_gap_m, 0.0, False, False
    distance = actors.config.obstacle_station_m - ego_s
    course: bool | None = False
    if alive:
        boxes = actors.local_boxes
        if boxes is None:
            raise P7Error("HAZARD_ACTORS_NOT_PREPARED")
        lead_pose = lead.get_transform()
        cargo_pose = cargo.get_transform()
        lead_s = actors.corridor.coordinates(lead_pose)[0]
        source_reference = planar(actors.corridor.transform_at(lead_s))
        lead_lateral = compute_lane_relative_geometry(planar(lead_pose), source_reference).lateral_error_m
        origin = actors.corridor.origin
        bounds = []
        for box, transform in ((boxes.ego, ego_pose), (boxes.cargo, cargo_pose)):
            corners = [(vertex.x - origin.x, vertex.y - origin.y)
                       for vertex in box.get_world_vertices(transform)]
            along = [x * cos(origin.yaw_rad) + y * sin(origin.yaw_rad) for x, y in corners]
            across = [-x * sin(origin.yaw_rad) + y * cos(origin.yaw_rad) for x, y in corners]
            bounds.append((min(along), max(along), min(across), max(across)))
        ego_bounds, cargo_bounds = bounds
        distance = cargo_bounds[0] - ego_bounds[1]
        course = ego_bounds[2] <= cargo_bounds[3] and ego_bounds[3] >= cargo_bounds[2]
        camera = camera_transform(actors.ego)
        clear = projected_bounds(boxes.lead, camera, transform=lead_pose).disjoint(
            projected_bounds(boxes.cargo, camera, transform=cargo_pose)
        )
        expected = actors.corridor.transform_at(min(lead_s, actors.corridor.points[-1].distance_m), lateral_fraction=1)
        expected_lateral = compute_lane_relative_geometry(planar(expected), source_reference).lateral_error_m
        complete = actors.complete and abs(lead_lateral - expected_lateral) < 0.15
    velocity = actors.ego.get_velocity()
    obstacle_velocity = cargo.get_velocity() if alive else carla.Vector3D()
    yaw = actors.corridor.origin.yaw_rad
    relative_x, relative_y = velocity.x - obstacle_velocity.x, velocity.y - obstacle_velocity.y
    closing = relative_x * cos(yaw) + relative_y * sin(yaw)
    if abs(-relative_x * sin(yaw) + relative_y * cos(yaw)) > 1e-6:
        course = None
    waypoint = actors.world_map.get_waypoint(ego_pose.location, project_to_road=False, lane_type=carla.LaneType.Driving)
    route_valid = (waypoint is not None and waypoint.road_id == actors.corridor.points[0].road_id
                   and waypoint.section_id == actors.corridor.points[0].section_id
                   and abs(compute_lane_relative_geometry(planar(ego_pose), actors.corridor.origin).heading_error_rad) < 0.2)
    return HazardObservation(
        timestamp, ego_s, lead_s, lead_lateral, actors.corridor.target_lane_id or 0,
        complete, clear, distance, closing, velocity.length(), course, driver,
        input_source="DEV_KEYBOARD_INTENT_NOT_APPLIED_TO_EGO", route_valid=route_valid,
        actors_alive=alive, obstacle_stationary=actors.obstacle_stationary,
        lead_path_clear=not any(in_hazard_zone(actors.corridor, other.get_transform()) for other in foreign),
        p5_active=False,
        collision_actor_id=None if collision is None else collision.other_id,
        collision_frame=None if collision is None else collision.frame,
        collision_simulation_s=None if collision is None else collision.simulation_s,
        road_departure=waypoint is None,
    )
