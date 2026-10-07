from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Final, Protocol

import carla

from src.experiment.noa_runtime import CarlaNoARuntimeWaypoint
from src.scenario.research_noa_config import (
    DEFAULT_RESEARCH_VEHICLE_BLUEPRINT,
    ResearchNoARunConfig,
    ResearchNoARunMode,
    parse_arguments,
)
from src.scenario.research_noa_lateral_trace import LateralValidationTraceWriter
from src.scenario.research_noa_lateral_validation import (
    LaneIdentity,
    LateralValidationCase,
    LateralValidationInitialCondition,
    LateralValidationInitialState,
    LateralValidationPlacement,
    LateralValidationVehicleMovingError,
    apply_initial_condition,
    validate_initial_state,
    validate_stationary_speed,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_session import ResearchNoAPreflightError
from src.scenario.research_noa_transmission import MonotonicClock
from src.scenario.research_noa_types import (
    ResearchNoAMap,
    ResearchNoAVehicle,
    ResearchNoAWorld,
)

EXPECTED_DURATION_SECONDS: Final = 20.0


@dataclass(frozen=True, slots=True)
class LateralValidationRunConfig:
    run_id: str
    condition: LateralValidationInitialCondition
    research_config: ResearchNoARunConfig


class LateralValidationProtocolError(ValueError):
    def __init__(
        self,
        field: str,
        actual: str | float | bool,
        expected: str | float | bool,
    ) -> None:
        self.field = field
        self.actual = actual
        self.expected = expected
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.field} must be {self.expected!r}, got {self.actual!r}"


class LateralValidationSettleSession(Protocol):
    world: ResearchNoAWorld
    hero: ResearchNoAVehicle
    config: ResearchNoARunConfig
    monotonic_clock: MonotonicClock


class LateralValidationRunner(ResearchNoARunner):
    def __init__(self, config: LateralValidationRunConfig) -> None:
        self._validation_config = config
        self._trace_writer = LateralValidationTraceWriter(
            config.run_id,
            config.condition,
            sys.stdout,
        )
        self._requested_spawn_transform: carla.Transform | None = None
        self._original_lane_identity: LaneIdentity | None = None
        super().__init__(config.research_config, control_trace=self._trace_writer)

    @property
    def requested_spawn_transform(self) -> carla.Transform:
        if self._requested_spawn_transform is None:
            raise ResearchNoAPreflightError("validation spawn has not been selected")
        return self._requested_spawn_transform

    @property
    def original_lane_identity(self) -> LaneIdentity:
        if self._original_lane_identity is None:
            raise ResearchNoAPreflightError("validation spawn has not been selected")
        return self._original_lane_identity

    @property
    def trace_writer(self) -> LateralValidationTraceWriter:
        return self._trace_writer

    def _select_spawn(
        self,
        world_map: ResearchNoAMap,
    ) -> tuple[carla.Transform, CarlaNoARuntimeWaypoint]:
        base_transform, waypoint = super()._select_spawn(world_map)
        requested = apply_initial_condition(
            base_transform,
            self._validation_config.condition,
        )
        self._requested_spawn_transform = requested
        self._original_lane_identity = LaneIdentity(
            int(waypoint.road_id),
            int(waypoint.section_id),
            int(waypoint.lane_id),
        )
        return requested, waypoint


def parse_lateral_validation_arguments(
    argv: Sequence[str] | None = None,
) -> LateralValidationRunConfig:
    parser = argparse.ArgumentParser(
        description="Run one developer-only lateral initial-condition validation.",
        epilog=(
            "Pass the normal research_noa live-smoke arguments after these "
            "validation arguments; all control values remain explicit."
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case", required=True, type=LateralValidationCase)
    parser.add_argument("--position-offset-m", required=True, type=float)
    parser.add_argument("--heading-offset-rad", required=True, type=float)
    validation, research_arguments = parser.parse_known_args(argv)
    condition = LateralValidationInitialCondition(
        validation.case,
        validation.position_offset_m,
        validation.heading_offset_rad,
    )
    research_config = parse_arguments(research_arguments)
    _validate_protocol(research_config)
    return LateralValidationRunConfig(validation.run_id, condition, research_config)


def _validate_protocol(config: ResearchNoARunConfig) -> None:
    if not isfinite(config.timeout) or config.timeout <= 0.0:
        raise LateralValidationProtocolError(
            "timeout",
            config.timeout,
            "finite and > 0",
        )
    longitudinal = config.control_config.longitudinal
    lateral = config.control_config.lateral
    expected_values = (
        ("mode", config.mode.value, ResearchNoARunMode.LIVE_SMOKE.value),
        ("duration", config.duration, EXPECTED_DURATION_SECONDS),
        (
            "vehicle_blueprint",
            config.vehicle_blueprint,
            DEFAULT_RESEARCH_VEHICLE_BLUEPRINT,
        ),
        ("front_camera_only", config.front_camera_only, True),
        ("target_speed_kmh", longitudinal.target_speed_kmh, 10.0),
        ("speed_deadband_kmh", longitudinal.speed_deadband_kmh, 0.2),
        ("acceleration_gain", longitudinal.acceleration_gain, 0.1),
        ("braking_gain", longitudinal.braking_gain, 0.1),
        ("integral_gain", longitudinal.integral_gain, 0.02),
        ("max_throttle", longitudinal.max_throttle, 0.4),
        ("max_brake", longitudinal.max_brake, 0.5),
        ("lateral_error_gain", lateral.lateral_error_gain, 0.2),
        ("heading_error_gain", lateral.heading_error_gain, 0.5),
        ("lateral_deadband_m", lateral.lateral_deadband_m, 0.1),
        ("heading_deadband_rad", lateral.heading_deadband_rad, 0.05),
        ("max_steering", lateral.max_steering, 0.15),
    )
    for field, actual, expected in expected_values:
        if actual != expected:
            raise LateralValidationProtocolError(field, actual, expected)


def wait_for_passive_spawn_settle(
    session: LateralValidationSettleSession,
    placement: LateralValidationPlacement,
) -> LateralValidationInitialState:
    deadline = session.monotonic_clock() + session.config.timeout
    while True:
        try:
            validate_stationary_speed(session.hero)
            return validate_initial_state(
                session.hero,
                session.world.get_map(),
                placement,
            )
        except LateralValidationVehicleMovingError as moving_error:
            remaining = deadline - session.monotonic_clock()
            if remaining <= 0.0:
                raise
            try:
                session.world.wait_for_tick(remaining)
            except RuntimeError:
                if session.monotonic_clock() >= deadline:
                    raise moving_error from None
                raise


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_lateral_validation_arguments(argv)
    runner = LateralValidationRunner(config)
    with runner.session() as session:
        initial_state = wait_for_passive_spawn_settle(
            session,
            LateralValidationPlacement(
                runner.requested_spawn_transform,
                config.condition,
                runner.original_lane_identity,
            ),
        )
        runner.trace_writer.write_preflight(initial_state)
        report = session.run()
    if report is None:
        raise ResearchNoAPreflightError("lateral validation requires live smoke mode")
    print(report.format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
