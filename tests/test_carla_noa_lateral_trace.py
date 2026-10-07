from __future__ import annotations

import pytest

from src.experiment.lane_geometry import LaneGeometryObservation
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.vehicle.carla_noa_simulation_control import (
    CarlaNoAControlObservation,
    SimulationTimeCarlaNoAControlBackend,
)
from tests.lateral_validation_fakes import DetailedFakeLaneGeometryAdapter
from tests.test_carla_noa_control import FakeVehicle


def test_step_trace_preserves_exact_geometry_used_for_steering() -> None:
    lane_adapter = DetailedFakeLaneGeometryAdapter(LaneGeometryObservation(0.5, 0.2))
    backend = SimulationTimeCarlaNoAControlBackend(
        FakeVehicle(),
        lane_adapter,
        LongitudinalControlConfig(10.0, 1.0, 0.1, 0.1, 0.4, 0.5, 0.02),
        LateralControlConfig(0.2, 0.5, 0.1, 0.05, 0.15),
    )
    backend.enter_noa_control()

    command = backend.step_from_observation(CarlaNoAControlObservation(7, 1.5, 9.0))

    assert backend.last_step.lane_geometry.geometry == LaneGeometryObservation(0.5, 0.2)
    assert backend.last_step.command.steering == pytest.approx(-0.15)
    assert command.steering == backend.last_step.command.steering
