import carla


class Waypoint:
    def __init__(self, x=0, y=0, lane=-1, *, right=True):
        self.transform = carla.Transform(carla.Location(x=x, y=y))
        self.road_id, self.section_id, self.lane_id = 1, 0, lane
        self.lane_width = 3.5
        self.lane_type = carla.LaneType.Driving
        self.lane_change = carla.LaneChange.Right
        self.is_junction = False
        self.right = right

    def next(self, distance):
        return [Waypoint(self.transform.location.x + distance, self.transform.location.y, self.lane_id, right=self.right)]

    def get_right_lane(self):
        return Waypoint(self.transform.location.x, self.transform.location.y + 3.5, self.lane_id - 1) if self.right else None


class Blueprint:
    def __init__(self, identifier):
        self.id = identifier
        self.attributes = {}

    def has_attribute(self, name):
        return name == "role_name"

    def set_attribute(self, name, value):
        self.attributes[name] = value


class Actor:
    def __init__(self, identifier, type_id="vehicle.fake", transform=None):
        self.id, self.type_id = identifier, type_id
        self.transform = transform or carla.Transform()
        self._bounding_box = (carla.BoundingBox(carla.Location(z=0.2), carla.Vector3D(0.25, 0.25, 0.2))
                              if type_id.startswith("static.") else
                              carla.BoundingBox(carla.Location(z=0.7), carla.Vector3D(2.2, 0.9, 0.7)))
        self.bounding_box_reads = 0
        self.is_alive = True
        self.mutations = []

    @property
    def bounding_box(self):
        self.bounding_box_reads += 1
        return self._bounding_box

    def get_transform(self):
        return carla.Transform(self.transform.location, self.transform.rotation)

    def get_location(self):
        return self.transform.location

    def get_velocity(self):
        return carla.Vector3D()

    def set_transform(self, transform):
        self.mutations.append("transform")
        self.transform = transform

    def set_simulate_physics(self, enabled):
        self.mutations.append(("physics", enabled))

    def destroy(self):
        self.mutations.append("destroy")
        self.is_alive = False
        return True


class Map:
    name = "FAKE_MAP_NOT_LIVE"

    def get_waypoint(self, location, **kwargs):
        return Waypoint(location.x, location.y)

    def get_spawn_points(self):
        return [carla.Transform(carla.Location(x=x)) for x in (-200, -150, -100)]


class World:
    def __init__(self, *, fail_type=None, missing_type=None, alias=None):
        self.fail_type, self.missing_type, self.alias = fail_type, missing_type, alias
        self.spawned = []

    def get_blueprint_library(self):
        return self

    def find(self, identifier):
        if identifier == self.missing_type:
            raise RuntimeError("missing fake blueprint")
        return Blueprint(identifier)

    def try_spawn_actor(self, blueprint, transform):
        if blueprint.id == self.fail_type:
            return None
        if self.alias is not None:
            return self.alias
        actor = Actor(100 + len(self.spawned), blueprint.id, transform)
        self.spawned.append(actor)
        return actor

    def get_map(self):
        return Map()
