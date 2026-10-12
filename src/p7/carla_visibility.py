from math import isfinite

import carla

from src.p7.geometry import ProjectionBox
from src.p7.types import P7Error
from src.scenario.driver_view import DriverViewConfig


def camera_transform(ego: carla.Actor) -> carla.Transform:
    vehicle = ego.get_transform()
    cockpit = DriverViewConfig().cockpit_transform
    location = vehicle.transform(carla.Location(x=cockpit.x, y=cockpit.y, z=cockpit.z))
    return carla.Transform(location, carla.Rotation(
        pitch=vehicle.rotation.pitch + cockpit.pitch,
        yaw=vehicle.rotation.yaw + cockpit.yaw, roll=vehicle.rotation.roll + cockpit.roll))


def projected_bounds(
    bounding_box: carla.BoundingBox,
    camera: carla.Transform,
    *,
    transform: carla.Transform,
) -> ProjectionBox:
    if min(bounding_box.extent.x, bounding_box.extent.y, bounding_box.extent.z) <= 0:
        raise P7Error("ACTOR_BOUNDING_BOX_UNAVAILABLE")
    inverse = camera.get_inverse_matrix()
    projected = []
    for vertex in bounding_box.get_world_vertices(transform):
        vector = (vertex.x, vertex.y, vertex.z, 1)
        x, y, z = (sum(row[i] * vector[i] for i in range(4)) for row in inverse[:3])
        if x <= 0 or not all(isfinite(value) for value in (x, y, z)):
            raise P7Error("HAZARD_PROJECTION_BEHIND_CAMERA_OR_INVALID")
        projected.append((y / x, z / x))
    return ProjectionBox(min(p[0] for p in projected), min(p[1] for p in projected),
                         max(p[0] for p in projected), max(p[1] for p in projected))
