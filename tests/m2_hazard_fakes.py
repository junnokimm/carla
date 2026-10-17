"""CARLA math objects + deterministic fake physics, no client/device access."""

from math import cos, degrees, radians, sin, tan
from types import SimpleNamespace

import carla

from tests.research_noa_fakes import (
    FakeActorList,
    FakeBlueprint,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
)


class Waypoint:
    road_id = 47
    section_id = 0
    lane_width = 3.5
    lane_type = carla.LaneType.Driving
    lane_change = carla.LaneChange.Both
    is_junction = False

    def __init__(self, x, lane=-3):
        self.lane_id = lane
        self.transform = carla.Transform(carla.Location(x=x, y=(lane + 3) * 3.5))

    def next(self, distance):
        return [Waypoint(self.transform.location.x + distance, self.lane_id)]

    def get_left_lane(self):
        return (
            Waypoint(self.transform.location.x, self.lane_id + 1)
            if self.lane_id < -1
            else None
        )

    def get_right_lane(self):
        return Waypoint(self.transform.location.x, self.lane_id - 1)


class Map:
    name = "Town04"

    def get_waypoint(self, location, project_to_road=True, lane_type=None):
        return Waypoint(location.x, -3 + round(location.y / 3.5))

    def get_spawn_points(self):
        return [carla.Transform() for _ in range(345)]


class Actor(FakeResearchVehicle):
    def __init__(
        self, identifier=42, blueprint="vehicle.mercedes.coupe_2020", pose=None
    ):
        super().__init__()
        self.id, self.type_id = identifier, blueprint
        self.transform = pose or carla.Transform()
        self.velocity = carla.Vector3D()
        self.bounding_box = carla.BoundingBox(
            carla.Location(z=0.7), carla.Vector3D(2.3, 0.9, 0.7)
        )
        if blueprint.startswith("static"):
            self.bounding_box = carla.BoundingBox(
                carla.Location(), carla.Vector3D(0.4, 0.4, 0.4)
            )
        self.is_alive = True
        self.transform_commands = []
        self.physics_commands = []

    def set_target_velocity(self, velocity):
        self.velocity = velocity

    def set_transform(self, pose):
        self.transform_commands.append(pose)
        self.transform = pose

    def set_simulate_physics(self, value):
        self.physics_commands.append(value)

    def listen(self, callback):
        self.callback = callback

    def stop(self):
        pass

    def destroy(self):
        self.is_alive = False
        return super().destroy()

    def physics(self, dt):
        speed = max(
            0,
            self.velocity.length()
            + (self.current_control.throttle * 3 - self.current_control.brake * 6) * dt,
        )
        yaw = (
            radians(self.transform.rotation.yaw)
            + speed / 2.8 * tan(self.current_control.steer * 0.6) * dt
        )
        p = self.transform.location
        self.transform = carla.Transform(
            carla.Location(
                x=p.x + speed * cos(yaw) * dt, y=p.y + speed * sin(yaw) * dt, z=p.z
            ),
            carla.Rotation(yaw=degrees(yaw)),
        )
        self.velocity = carla.Vector3D(x=speed * cos(yaw), y=speed * sin(yaw))


class Snapshot:
    def __init__(self, world):
        self.frame = world.frame
        self.timestamp = SimpleNamespace(elapsed_seconds=world.simulation_seconds)
        self.actors = {}
        for actor in world.actors:
            if actor.is_alive:
                pose, velocity = actor.get_transform(), actor.get_velocity()
                self.actors[actor.id] = SimpleNamespace(
                    get_transform=lambda p=pose: p, get_velocity=lambda v=velocity: v
                )

    def find(self, identifier):
        return self.actors.get(identifier)


class World(FakeResearchWorld):
    def __init__(self, ego=None, **kwargs):
        super().__init__(ego or Actor(), **kwargs)
        self.map = Map()
        self.actors = [self.vehicle]
        self.callbacks = {}
        self.inventory_count = 0
        self.spawn_failure = None

    def get_actors(self):
        self.inventory_count += 1
        return FakeActorList(a for a in self.actors if a.is_alive)

    def get_blueprint_library(self):
        def find(identifier):
            blueprint = FakeBlueprint()
            blueprint.id = identifier
            return blueprint

        return SimpleNamespace(find=find)

    def try_spawn_actor(self, blueprint, pose):
        if blueprint.id == self.spawn_failure:
            return None
        actor = Actor(100 + len(self.actors), blueprint.id, pose)
        self.actors.append(actor)
        return actor

    def spawn_actor(self, blueprint, transform, attach_to=None):
        if attach_to is not None:
            return self.try_spawn_actor(blueprint, transform)
        self.spawn_calls.append((blueprint, transform))
        return self.vehicle

    def get_snapshot(self):
        self.get_snapshot_count += 1
        return Snapshot(self)

    def on_tick(self, callback):
        self.callbacks[1] = callback
        return 1

    def remove_on_tick(self, identifier):
        del self.callbacks[identifier]

    def step(self, dt, ego_speed=12, ego_y=None):
        self.simulation_seconds += dt
        self.frame += 1
        if self.monotonic_clock is not None:
            self.monotonic_clock.advance(dt)
        p = self.vehicle.transform.location
        self.vehicle.transform = carla.Transform(
            carla.Location(
                x=p.x + ego_speed * dt, y=p.y if ego_y is None else ego_y, z=p.z
            )
        )
        self.vehicle.velocity = carla.Vector3D(x=ego_speed)
        for actor in self.actors[1:]:
            if actor.is_alive and actor.type_id.startswith("vehicle"):
                actor.physics(dt)
        snapshot = self.get_snapshot()
        for callback in self.callbacks.values():
            callback(snapshot)
        return snapshot


class TM(FakeTrafficManager):
    def collision_detection(self, ego, cargo, enabled):
        self.calls.append(("collision_detection", ego.id, cargo.id, enabled))


def front_feed(ego):
    def transform():
        p = ego.get_transform()
        return carla.Transform(p.transform(carla.Location(x=0.5, z=1.2)), p.rotation)

    return SimpleNamespace(
        role="front",
        sensor=SimpleNamespace(
            id=900,
            get_transform=transform,
            attributes={"fov": "100", "image_size_x": "1920", "image_size_y": "1080"},
        ),
    )
