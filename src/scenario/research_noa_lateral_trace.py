from __future__ import annotations

import csv
from typing import TextIO

from src.scenario.research_noa_lateral_validation import (
    LateralValidationInitialCondition,
    LateralValidationInitialState,
)
from src.vehicle.carla_noa_simulation_control import CarlaNoAControlStep


class LateralValidationTraceWriter:
    def __init__(
        self,
        run_id: str,
        condition: LateralValidationInitialCondition,
        output: TextIO,
    ) -> None:
        self._run_id = run_id
        self._condition = condition
        self._writer = csv.writer(output, lineterminator="\n")
        self._header_written = False

    def write_preflight(self, state: LateralValidationInitialState) -> None:
        geometry = state.geometry
        self._writer.writerow(
            (
                "lateral_preflight",
                self._run_id,
                state.condition.case.value,
                state.condition.position_offset_m,
                state.condition.heading_offset_rad,
                state.speed_kmh,
                state.minimum_lane_margin_m,
                geometry.geometry.lateral_error_m,
                geometry.geometry.heading_error_rad,
                geometry.road_id,
                geometry.section_id,
                geometry.lane_id,
                geometry.lane_width_m,
                geometry.vehicle_pose.x,
                geometry.vehicle_pose.y,
                geometry.vehicle_pose.yaw_rad,
                geometry.waypoint_pose.x,
                geometry.waypoint_pose.y,
                geometry.waypoint_pose.yaw_rad,
                "live_vehicle_transform_before_prime_and_noa",
            )
        )

    def __call__(self, step: CarlaNoAControlStep) -> None:
        if not self._header_written:
            self._writer.writerow(
                (
                    "lateral_trace",
                    "run_id",
                    "case",
                    "requested_position_offset_m",
                    "requested_heading_offset_rad",
                    "frame",
                    "simulation_time_seconds",
                    "speed_timing_provenance",
                    "geometry_timing_provenance",
                    "actual_pi_dt_seconds",
                    "target_speed_kmh",
                    "measured_speed_kmh",
                    "integral_effort",
                    "commanded_throttle",
                    "commanded_brake",
                    "commanded_steering",
                    "lateral_error_m",
                    "heading_error_rad",
                    "road_id",
                    "section_id",
                    "lane_id",
                    "lane_width_m",
                    "vehicle_x",
                    "vehicle_y",
                    "vehicle_yaw_rad",
                    "waypoint_x",
                    "waypoint_y",
                    "waypoint_yaw_rad",
                )
            )
            self._header_written = True
        geometry = step.lane_geometry
        self._writer.writerow(
            (
                "lateral_trace",
                self._run_id,
                self._condition.case.value,
                self._condition.position_offset_m,
                self._condition.heading_offset_rad,
                step.frame,
                step.simulation_time_seconds,
                "world_snapshot_actor_velocity",
                "live_vehicle_transform_after_snapshot_before_command",
                step.delta_seconds,
                step.target_speed_kmh,
                step.measured_speed_kmh,
                step.integral_effort,
                step.command.throttle,
                step.command.brake,
                step.command.steering,
                geometry.geometry.lateral_error_m,
                geometry.geometry.heading_error_rad,
                geometry.road_id,
                geometry.section_id,
                geometry.lane_id,
                geometry.lane_width_m,
                geometry.vehicle_pose.x,
                geometry.vehicle_pose.y,
                geometry.vehicle_pose.yaw_rad,
                geometry.waypoint_pose.x,
                geometry.waypoint_pose.y,
                geometry.waypoint_pose.yaw_rad,
            )
        )
