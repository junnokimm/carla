from __future__ import annotations

from io import StringIO

from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_lateral_trace import LateralValidationTraceWriter
from src.scenario.research_noa_lateral_validation import (
    LateralValidationCase,
    LateralValidationInitialCondition,
)
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext
from src.vehicle.carla_noa_simulation_control import CarlaNoAControlStep


def test_trace_row_combines_snapshot_pi_command_and_exact_controller_geometry() -> None:
    output = StringIO()
    condition = LateralValidationInitialCondition(
        LateralValidationCase.HEADING_RIGHT,
        position_offset_m=0.0,
        heading_offset_rad=0.06,
    )
    writer = LateralValidationTraceWriter("heading-right-01", condition, output)
    geometry = CarlaLaneGeometryContext(
        geometry=LaneGeometryObservation(0.12, 0.06),
        road_id=36,
        section_id=0,
        lane_id=-2,
        lane_width_m=3.5,
        vehicle_pose=PlanarPose(388.2, -98.9, 1.58),
        waypoint_pose=PlanarPose(388.25, -98.95, 1.581),
    )
    step = CarlaNoAControlStep(
        frame=321,
        simulation_time_seconds=12.5,
        delta_seconds=0.05,
        target_speed_kmh=10.0,
        measured_speed_kmh=9.0,
        integral_effort=0.123,
        lane_geometry=geometry,
        command=NoAControlCommand(0.2, 0.0, -0.054),
    )

    writer(step)

    rows = [line.split(",") for line in output.getvalue().splitlines()]
    assert rows[0][0:4] == [
        "lateral_trace",
        "run_id",
        "case",
        "requested_position_offset_m",
    ]
    assert rows[1][0:4] == ["lateral_trace", "heading-right-01", "heading-right", "0.0"]
    assert rows[1][5:8] == ["321", "12.5", "world_snapshot_actor_velocity"]
    assert rows[1][13:18] == ["0.2", "0.0", "-0.054", "0.12", "0.06"]
    assert rows[1][18:22] == ["36", "0", "-2", "3.5"]
