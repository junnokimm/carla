from __future__ import annotations

from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext
from tests.test_carla_noa_control import FakeLaneGeometryAdapter, FakeVehicle


class DetailedFakeLaneGeometryAdapter(FakeLaneGeometryAdapter):
    def observe_with_context(
        self,
        vehicle: FakeVehicle,
    ) -> CarlaLaneGeometryContext:
        observation = self.observe(vehicle)
        pose = PlanarPose(0.0, 0.0, 0.0)
        return CarlaLaneGeometryContext(
            geometry=observation,
            road_id=36,
            section_id=0,
            lane_id=-2,
            lane_width_m=3.5,
            vehicle_pose=pose,
            waypoint_pose=pose,
        )


def make_geometry_context(
    lateral_error_m: float = 0.0,
    heading_error_rad: float = 0.0,
) -> CarlaLaneGeometryContext:
    pose = PlanarPose(0.0, 0.0, 0.0)
    return CarlaLaneGeometryContext(
        geometry=LaneGeometryObservation(lateral_error_m, heading_error_rad),
        road_id=36,
        section_id=0,
        lane_id=-2,
        lane_width_m=3.5,
        vehicle_pose=pose,
        waypoint_pose=pose,
    )
