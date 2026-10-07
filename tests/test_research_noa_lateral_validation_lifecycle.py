from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import carla
import pytest

from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.scenario import research_noa_lateral_validation_cli
from src.scenario.research_noa_lateral_validation import (
    LaneIdentity,
    LateralValidationCase,
    LateralValidationInitialCondition,
    LateralValidationInitialState,
    LateralValidationPlacement,
    LateralValidationPreflightError,
    validate_initial_state,
)
from src.scenario.research_noa_lateral_validation_cli import (
    LateralValidationRunConfig,
    parse_lateral_validation_arguments,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryContext,
    CarlaLaneGeometryUnavailableError,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_research_noa_lateral_validation_cli import _arguments


class MainSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def run(self) -> MainReport:
        self.events.append("run")
        return MainReport()


class MainReport:
    def format(self) -> str:
        return "validation report"


class MainTraceWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def write_preflight(self, initial_state: LateralValidationInitialState) -> None:
        self.events.append("write")


class MainRunner:
    def __init__(self, events: list[str]) -> None:
        self.requested_spawn_transform = carla.Transform()
        self.original_lane_identity = LaneIdentity(36, 0, -3)
        self.trace_writer = MainTraceWriter(events)
        self._session = MainSession(events)

    @contextmanager
    def session(self) -> Iterator[MainSession]:
        yield self._session


def _initial_state(
    config: LateralValidationRunConfig,
) -> LateralValidationInitialState:
    pose = PlanarPose(0.0, 0.0, 0.0)
    geometry = CarlaLaneGeometryContext(
        LaneGeometryObservation(0.0, 0.0),
        36,
        0,
        -3,
        3.5,
        pose,
        pose,
    )
    return LateralValidationInitialState(config.condition, geometry, 0.0, 0.5)


def test_runner_context_preserves_validation_error_and_cleans_owned_actor() -> None:
    parsed = parse_lateral_validation_arguments(_arguments())
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        parsed.research_config,
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )
    expected = LateralValidationPreflightError("vehicle must be stationary")

    with pytest.raises(LateralValidationPreflightError) as caught, runner.session():
        raise expected

    assert caught.value is expected
    assert viewer_factory.created[0].close_count == 1
    assert vehicle.destroy_count == 1


def test_missing_lane_center_becomes_validation_error_and_session_cleans_up() -> None:
    parsed = parse_lateral_validation_arguments(_arguments())
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        parsed.research_config,
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )
    condition = LateralValidationInitialCondition(
        LateralValidationCase.BASELINE,
        0.0,
        0.0,
    )

    with (
        pytest.raises(LateralValidationPreflightError) as caught,
        runner.session() as session,
    ):
        vehicle.transform = session.spawn_transform
        world.map.waypoint = None
        validate_initial_state(
            vehicle,
            world.map,
            LateralValidationPlacement(
                session.spawn_transform,
                condition,
                LaneIdentity(36, 0, -3),
            ),
        )

    assert isinstance(caught.value.__cause__, CarlaLaneGeometryUnavailableError)
    assert viewer_factory.created[0].close_count == 1
    assert vehicle.destroy_count == 1


def test_main_settles_passively_before_preflight_write_and_transmission_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = parse_lateral_validation_arguments(_arguments())
    events: list[str] = []
    runner = MainRunner(events)
    expected = _initial_state(config)

    def settle(
        session: MainSession,
        placement: LateralValidationPlacement,
    ) -> LateralValidationInitialState:
        events.append("settle")
        return expected

    monkeypatch.setattr(
        research_noa_lateral_validation_cli,
        "parse_lateral_validation_arguments",
        lambda argv: config,
    )
    monkeypatch.setattr(
        research_noa_lateral_validation_cli,
        "LateralValidationRunner",
        lambda parsed: runner,
    )
    monkeypatch.setattr(
        research_noa_lateral_validation_cli,
        "wait_for_passive_spawn_settle",
        settle,
    )

    exit_code = research_noa_lateral_validation_cli.main([])

    assert exit_code == 0
    assert events == ["settle", "write", "run"]
