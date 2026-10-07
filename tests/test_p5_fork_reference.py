from dataclasses import replace
from pathlib import Path

import carla
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, Module2Condition
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
)
from src.experiment.lateral_control import LateralControlConfig, compute_lateral_control
from src.scenario.research_exit_runtime import ResearchExitObservationSource
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_exit_route import (
    SwitchableRouteLaneGeometrySource,
    load_exit_route_manifest,
)
from tests.test_p5_boundary_contracts import ACTIVE, stamp
from tests.test_p5_oracle_blockers import SnapshotHolder
from tests.test_p5_route_reference import OrdinaryGeometry, Vehicle


def confirmed_exit47():
    route = load_exit_route_manifest(Path("config/town04_exit_routes_dev_v3.json")).route("town04-exit-47")
    reference = SwitchableRouteLaneGeometrySource(OrdinaryGeometry())
    subject = ExitAssistanceCoordinator(
        ExitAssistanceConfig(ExperimentModule.MODULE_2, Module2Condition.NOA_L2,
                             ExitContext.MODULE, "fork-regression", route, 1000.0, 5.0, 0.15),
        reference,
    )
    subject.observe(ExitObservation(stamp(1), 1039.0, -3.5, 47, 0, -3, 1, 0.0,
                                    route_point_index=200, target_boundary_valid=True))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(2))
    subject.update(DriverInput(exit_confirm_requested=True), ACTIVE, stamp(3))
    return subject, reference


def through_observation(route, x: float, y: float, frame: int) -> ExitObservation:
    transform = carla.Transform(carla.Location(x=x, y=y), carla.Rotation(yaw=-90.224854))
    sample = ResearchSnapshotSample(stamp(frame), transform, 774, 0, -4, 3.5,
                                    transform, None, None, None, carla.VehicleControl(), "off")
    observation = ResearchExitObservationSource(route, SnapshotHolder(sample)).observe()
    return replace(observation, carla_frame=frame)


def test_actual_shared_fork_identity_does_not_cancel_exit_reference() -> None:
    subject, reference = confirmed_exit47()
    observation = through_observation(subject.config.route, 15.25317668914795, -47.23847579956055, 4)
    subject.observe(observation)
    assert subject.status is ExitAssistanceStatus.CONFIRMED
    assert reference.active_route is subject.config.route
    assert observation.route_reference_distance_m == pytest.approx(0.0021571, abs=1e-6)
    vehicle = Vehicle(carla.Transform(carla.Location(x=15.194308, y=-62.23836), carla.Rotation(yaw=-90.224854)))
    geometry = reference.observe_with_context(vehicle).geometry
    command = compute_lateral_control(geometry.lateral_error_m, geometry.heading_error_rad,
                                     LateralControlConfig(0.2, 0.5, 0.1, 0.05, 0.15))
    assert command.steering == pytest.approx(0.15)


def test_separated_through_path_still_records_missed_and_cancels() -> None:
    subject, reference = confirmed_exit47()
    subject.observe(through_observation(subject.config.route, 15.174685, -67.238319, 4))
    assert subject.status is ExitAssistanceStatus.COMPLETED
    assert reference.active_route is None
    assert '"outcome":"MISSED"' in subject.events[-1].payload_json


def test_unknown_exit_distance_is_not_evidence_of_missed_branch() -> None:
    subject, reference = confirmed_exit47()
    subject.observe(ExitObservation(stamp(4), -1.0, 0.0, 774, 0, -4, 4, 0.1, route_matched=False))
    assert subject.status is ExitAssistanceStatus.CONFIRMED
    assert reference.active_route is not None


def test_reference_survives_lane_completion_and_shared_fork_until_exit() -> None:
    subject, reference = confirmed_exit47()
    subject.observe(ExitObservation(stamp(4), 1126.67, 0.0, 47, 0, -4, 4, 0.1,
                                    target_boundary_valid=True))
    assert reference.active_route is subject.config.route
    subject.observe(through_observation(subject.config.route, 15.25317668914795, -47.23847579956055, 5))
    assert reference.active_route is subject.config.route
    subject.observe(replace(
        through_observation(subject.config.route, 20.878963, -73.920525, 6),
        road_id=782, section_id=1, lane_id=-2,
    ))
    assert reference.active_route is subject.config.route
    subject.observe(replace(
        through_observation(subject.config.route, 34.791084, -91.433884, 7),
        road_id=34, section_id=0, lane_id=2,
    ))
    assert subject.status is ExitAssistanceStatus.COMPLETED
    assert reference.active_route is None
    assert '"outcome":"EXIT"' in subject.events[-1].payload_json
