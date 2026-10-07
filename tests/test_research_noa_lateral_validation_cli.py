from __future__ import annotations

from math import radians

import pytest

from src.scenario.research_noa_lateral_validation import LateralValidationCase
from src.scenario.research_noa_lateral_validation_cli import (
    LateralValidationProtocolError,
    LateralValidationRunner,
    parse_lateral_validation_arguments,
)
from tests.research_noa_fakes import FakeResearchVehicle, FakeResearchWorld

CONTROL_ARGUMENTS = [
    "--target-speed-kmh",
    "10",
    "--speed-deadband-kmh",
    "0.2",
    "--acceleration-gain",
    "0.1",
    "--braking-gain",
    "0.1",
    "--integral-gain",
    "0.02",
    "--max-throttle",
    "0.4",
    "--max-brake",
    "0.5",
    "--lateral-error-gain",
    "0.2",
    "--heading-error-gain",
    "0.5",
    "--lateral-deadband-m",
    "0.1",
    "--heading-deadband-rad",
    "0.05",
    "--max-steering",
    "0.15",
]


def _arguments() -> list[str]:
    return [
        "--run-id",
        "town04-position-right-01",
        "--case",
        "position-right",
        "--position-offset-m",
        "0.25",
        "--heading-offset-rad",
        "0",
        "--live-smoke",
        "--spawn-index",
        "0",
        "--duration",
        "20",
        "--vehicle-blueprint",
        "vehicle.mercedes.coupe_2020",
        "--front-camera-only",
        *CONTROL_ARGUMENTS,
    ]


def test_cli_parses_explicit_validation_case_and_research_protocol() -> None:
    parsed = parse_lateral_validation_arguments(_arguments())

    assert parsed.run_id == "town04-position-right-01"
    assert parsed.condition.case is LateralValidationCase.POSITION_RIGHT
    assert parsed.condition.position_offset_m == pytest.approx(0.25)
    assert parsed.research_config.duration == pytest.approx(20.0)
    assert parsed.research_config.front_camera_only is True


def test_cli_rejects_run_that_does_not_match_validation_protocol() -> None:
    arguments = _arguments()
    arguments[arguments.index("--duration") + 1] = "10"

    with pytest.raises(LateralValidationProtocolError) as caught:
        parse_lateral_validation_arguments(arguments)

    assert caught.value.field == "duration"


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1.0, 0.0])
def test_cli_rejects_nonfinite_or_nonpositive_passive_settle_timeout(
    timeout: float,
) -> None:
    arguments = [*_arguments(), "--timeout", str(timeout)]

    with pytest.raises(LateralValidationProtocolError) as caught:
        parse_lateral_validation_arguments(arguments)

    assert caught.value.field == "timeout"


def test_validation_runner_offsets_spawn_before_actor_creation() -> None:
    parsed = parse_lateral_validation_arguments(_arguments())
    world = FakeResearchWorld(FakeResearchVehicle())
    world.map.spawn_point.location.x = 10.0
    world.map.spawn_point.location.y = 20.0
    world.map.spawn_point.rotation.yaw = 90.0
    runner = LateralValidationRunner(parsed)

    transform, _ = runner._select_spawn(world.map)

    assert transform.location.x == pytest.approx(9.75)
    assert transform.location.y == pytest.approx(20.0)
    assert radians(transform.rotation.yaw - 90.0) == pytest.approx(0.0)
    assert runner.requested_spawn_transform is transform
    assert runner.original_lane_identity.road_id == 36
    assert runner.original_lane_identity.section_id == 0
    assert runner.original_lane_identity.lane_id == -3
