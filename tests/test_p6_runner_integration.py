from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.p6.config import P6Config
from src.p6.source import RealSourceUnavailableError
from src.scenario.research_noa import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p4_research_noa_integration import (
    IncrementingNanoseconds,
    make_module_two_config,
)


def test_real_p6_preflight_fails_before_vehicle_spawn(tmp_path: Path) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    config = replace(
        make_module_two_config(tmp_path, "p6-real"),
        p6=P6Config(schema_version=1, source_mode="real"),
    )
    runner = ResearchNoARunner(config, client=FakeClient(world))

    with pytest.raises(RealSourceUnavailableError) as caught, runner.session():
        pass

    assert caught.value.status == "BLOCKED_MODEL_SDK_UNCONFIRMED"
    assert world.spawn_calls == []


def test_synthetic_p6_uses_research_origin_and_stops_after_safety_brake(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    clock = IncrementingNanoseconds()
    config = replace(
        make_module_two_config(tmp_path, "p6-synthetic"),
        p6=P6Config(schema_version=1, source_mode="synthetic", sample_hz=1000),
    )
    runner = ResearchNoARunner(
        config,
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=clock,
        utc_ns=lambda: 1_800_000_000_000_000_000,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: None]
        session.run()
        assert session.p6 is not None
        gaze_path = session.p6.gaze_path

    rows = [json.loads(line) for line in gaze_path.read_text().splitlines()]
    assert rows[0]["origin_monotonic_ns"] == 1_000_000
    assert any(row["record_type"] == "gaze" for row in rows)
    assert vehicle.current_control.brake == config.control_config.longitudinal.max_brake
    assert vehicle.destroy_count == 1
