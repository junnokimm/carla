import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.p8.assignment import development_assignment
from src.p8.model import Mode
from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_noa_runtime import FakeVelocity
from tests.test_p8_operator_backend import StrictClock
from tests.test_p8_p5_live_drive import ROUTE_ID, p5_runner
from tests.test_research_noa_nominal_policy import nominal_config
from tests.test_research_noa_nominal_runtime import HoldingSpeedVehicle


@pytest.mark.parametrize("captured_speed", [111.0, float("nan")])
def test_blocked_capture_preserves_causal_event_order_with_shared_clock(
    tmp_path: Path, captured_speed: float,
) -> None:
    config = nominal_config(tmp_path)
    assert config.persistence is not None
    clock = StrictClock()
    services = SessionServices(
        tmp_path / "shared", config.persistence.assignment,
        str(config.persistence.run_id), Mode.DEV_REHEARSAL,
        monotonic_ns=clock.monotonic_ns, utc_ns=clock.utc_ns,
    )
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        replace(config, persistence=replace(config.persistence, output_dir=services.directory)),
        client=FakeClient(FakeResearchWorld(HoldingSpeedVehicle([0.0, captured_speed]))),
        viewer_factory=viewer_factory,
        driver_input_source=lambda: DriverInput(activation_requested=True),
        monotonic_ns=clock.monotonic_ns, utc_ns=clock.utc_ns,
        borrowed_log=services.borrowed_research_log(),
    )
    try:
        with runner.session() as session:
            viewer_factory.created[0].actions = [lambda: None]
            with pytest.raises(ResearchNoAActualSpeedSafetyError):
                session.run()
    finally:
        services.close()

    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    event_types = [row["event_type"] for row in rows]
    activation_index = event_types.index("activation_speed_hold")
    violation_index = event_types.index("speed_limit_violation")
    state_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "auto_state" and row["event_value"].endswith("NOA_ACTIVE")
    )
    manual_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "auto_state" and row["event_value"].endswith("MANUAL")
    )
    shutdown_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "disengagement" and row["event_value"] == "SAFETY_SHUTDOWN"
    )
    assert state_index < activation_index < manual_index < shutdown_index < violation_index
    timestamps = [int(row["host_monotonic_ns"]) for row in rows]
    assert timestamps == sorted(timestamps)
    assert json.loads(rows[activation_index]["event_value"])["status"] == "BLOCKED"


def test_existing_p5_cleanup_keeps_shutdown_rows_before_newer_events(tmp_path: Path) -> None:
    clock = StrictClock()
    services = SessionServices(
        tmp_path, replace(development_assignment(), route_id=ROUTE_ID),
        "p5-clock-regression", Mode.DEV_LIVE_DRIVE,
        monotonic_ns=clock.monotonic_ns, utc_ns=clock.utc_ns,
    )
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        p5_runner(services, world).config,
        client=FakeClient(world), viewer_factory=factory,
        driver_input_source=lambda: DriverInput(activation_requested=True),
        exit_route_validator=lambda *_: None,
        monotonic_ns=clock.monotonic_ns, utc_ns=clock.utc_ns,
        borrowed_log=services.borrowed_research_log(),
    )
    try:
        with runner.session() as session:
            factory.created[0].actions = [
                lambda: setattr(vehicle, "velocity", FakeVelocity(21.0 / 3.6, 0.0, 0.0))
            ]
            with pytest.raises(ResearchNoAActualSpeedSafetyError):
                session.run()
    finally:
        services.close()

    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    event_types = [row["event_type"] for row in rows]
    manual_index = next(
        index for index, row in enumerate(rows)
        if row["event_type"] == "auto_state" and row["event_value"].endswith("MANUAL")
    )
    shutdown_index = event_types.index("disengagement")
    assert manual_index < shutdown_index < event_types.index("lc_finished") < event_types.index("speed_limit_violation")
    timestamps = [int(row["host_monotonic_ns"]) for row in rows]
    assert timestamps == sorted(timestamps)
