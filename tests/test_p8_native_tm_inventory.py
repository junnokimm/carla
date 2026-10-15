from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation_interaction_types import AutomationInteractionConfig
from src.experiment.context import ExperimentModule, Module1Condition, StudyRunId
from src.p8.assignment import development_assignment
from src.p8.native_tm_diagnostics import NativeTrafficManagerDiagnosticsConfig
from src.scenario.research_noa_config import ResearchAutomationInteractionConfig
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_runtime import (
    ResearchNoAPreflightError,
    ResearchNoARunner,
)
from tests.research_noa_fakes import (
    FakeActorList,
    FakeClient,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeWorldSnapshot,
)
from tests.test_p8_native_tm_diagnostics_runtime import PassiveDiagnosticTrafficManager
from tests.test_p8_traffic_manager import _tm_runner_with_client


class VisibilityWorld(FakeResearchWorld):
    def __init__(self, vehicle: FakeResearchVehicle) -> None:
        self.clock = FakeMonotonicClock()
        super().__init__(vehicle, monotonic_clock=self.clock, tick_seconds=0.1)
        self.visible_frame = 1
        self.extra: tuple[FakeResearchVehicle, ...] = ()
        self.stale = False
        self.actor_error: RuntimeError | None = None
        self.inventory_frames: list[int] = []

    def get_actors(self) -> FakeActorList:
        self.inventory_frames.append(self.frame)
        if self.actor_error is not None:
            raise self.actor_error
        ego = (self.vehicle,) if self.frame >= self.visible_frame else ()
        return FakeActorList((*ego, *self.extra))

    def wait_for_tick(self, seconds: float) -> FakeWorldSnapshot:
        if self.stale:
            self.wait_for_tick_calls.append(seconds)
            return FakeWorldSnapshot(self.frame)
        return super().wait_for_tick(seconds)


def inventory_runner(
    tmp_path: Path,
) -> tuple[ResearchNoARunner, VisibilityWorld, PassiveDiagnosticTrafficManager]:
    runner, _, _, vehicle = _tm_runner_with_client()
    world = VisibilityWorld(vehicle)
    manager = PassiveDiagnosticTrafficManager(vehicle)
    runner._client = FakeClient(world, manager)
    runner._monotonic_clock = world.clock
    runner._config = replace(
        runner._config,
        timeout=0.25,
        run_id="inventory",
        automation_interaction=ResearchAutomationInteractionConfig(
            ExperimentModule.MODULE_1,
            Module1Condition.NO_SURT,
            AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
        ),
        native_tm_diagnostics=NativeTrafficManagerDiagnosticsConfig(8),
        persistence=ResearchPersistenceConfig(
            development_assignment(),
            StudyRunId("inventory"),
            tmp_path,
            ExperimentModule.MODULE_1,
            "TRAINING",
        ),
    )
    return runner, world, manager


def inventory_events(tmp_path: Path) -> list[dict]:
    path = next(tmp_path.glob("research_events_*.csv"))
    with path.open(encoding="utf-8", newline="") as source:
        return [
            json.loads(row["event_value"])
            for row in csv.DictReader(source)
            if row["event_type"] == "p8_native_tm_inventory"
        ]


def test_spawned_ego_waits_for_fresh_inventory_before_enabling(tmp_path: Path) -> None:
    # Given a successful spawn whose streamed inventory initially omits ego.
    runner, world, manager = inventory_runner(tmp_path)
    # When the real runner acquires its session.
    with runner.session():
        assert manager.enabled
        assert world.frame == 1
    # Then evidence distinguishes the wait and the successful fresh inventory.
    rows = inventory_events(tmp_path)
    assert [row["outcome"] for row in rows] == ["WAITING_FOR_EGO", "PASSED"]
    assert rows[-1]["attempt"] == 2
    assert rows[-1]["frame_before"] > rows[0]["frame_after"]
    assert rows[-1]["ego_actor_id"] == world.vehicle.id
    assert rows[-1]["vehicles"][0]["id"] == world.vehicle.id
    assert rows[-1]["elapsed_seconds"] == pytest.approx(0.1)
    assert isinstance(rows[-1]["host_monotonic_ns"], int)
    assert world.vehicle.destroy_count == 1


def test_missing_ego_times_out_without_enabling_and_is_cleaned(tmp_path: Path) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.visible_frame = 100
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_REJECTED"),
        runner.session(),
    ):
        pytest.fail("missing ego must not start a session")
    assert world.clock() == pytest.approx(0.25)
    assert world.wait_for_tick_calls == pytest.approx([0.25, 0.15, 0.05])
    assert not manager.enabled
    assert world.vehicle.destroy_count == 1
    assert not tuple(tmp_path.glob("native_tm_diagnostics*.jsonl"))
    assert inventory_events(tmp_path)[-1]["reason"] == "EGO_VISIBILITY_TIMEOUT"


@pytest.mark.parametrize(
    "actor_type",
    [
        "vehicle.mercedes.coupe_2020",
        "walker.pedestrian.0001",
        "static.prop.box01",
    ],
)
@pytest.mark.parametrize("ego_visible", [False, True])
def test_prohibited_inventory_rejected_without_waiting(
    tmp_path: Path,
    actor_type: str,
    ego_visible: bool,
) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.visible_frame = 0 if ego_visible else 1
    other = FakeResearchVehicle()
    other.id, other.type_id = 99, actor_type
    world.extra = (other,)
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_REJECTED"),
        runner.session(),
    ):
        pytest.fail("foreign actor must fail closed")
    assert world.wait_for_tick_calls == []
    assert not manager.enabled
    assert world.vehicle.destroy_count == 1
    assert other.destroy_count == 0
    rows = inventory_events(tmp_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "FAILED_CLOSED"
    assert rows[0]["prohibited"] == [{"id": 99, "type_id": actor_type}]


def test_inventory_query_error_is_persisted_and_fails_closed(tmp_path: Path) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.actor_error = RuntimeError("actor query unavailable")
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_FAILED"),
        runner.session(),
    ):
        pytest.fail("query failure must fail closed")
    assert world.wait_for_tick_calls == []
    assert not manager.enabled
    assert world.vehicle.destroy_count == 1
    assert "actor query unavailable" in inventory_events(tmp_path)[-1]["error"]


def test_stale_tick_never_rechecks_inventory_or_enables(tmp_path: Path) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.stale = True
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_REJECTED"),
        runner.session(),
    ):
        pytest.fail("stale frame must not be accepted as progress")
    assert len(world.wait_for_tick_calls) == 1
    assert world.inventory_frames == [0]
    assert not manager.enabled
    assert world.vehicle.destroy_count == 1
    assert inventory_events(tmp_path)[-1]["reason"] == "FRAME_NOT_ADVANCED"


def test_wait_timeout_is_persisted_and_cleans_ego(tmp_path: Path) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.wait_error = RuntimeError("time-out waiting for simulator")
    with (
        pytest.raises(ResearchNoAPreflightError, match="INVENTORY_FAILED"),
        runner.session(),
    ):
        pytest.fail("missing ticks must fail closed")
    assert len(world.wait_for_tick_calls) == 1
    assert not manager.enabled
    assert world.vehicle.destroy_count == 1
    assert inventory_events(tmp_path)[-1]["reason"] == "SNAPSHOT_WAIT_FAILED"


def test_nondiagnostic_runner_has_no_new_inventory_wait(tmp_path: Path) -> None:
    runner, world, _ = inventory_runner(tmp_path)
    runner._config = replace(runner._config, native_tm_diagnostics=None)
    world.actor_error = RuntimeError("must not query startup inventory")
    with runner.session():
        assert world.wait_for_tick_calls == []
    assert world.inventory_frames == []


def test_already_visible_ego_needs_no_tick(tmp_path: Path) -> None:
    runner, world, manager = inventory_runner(tmp_path)
    world.visible_frame = 0
    with runner.session():
        assert manager.enabled
    assert world.wait_for_tick_calls == []
    assert inventory_events(tmp_path)[-1]["outcome"] == "PASSED"
