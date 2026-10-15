from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, Stage
from src.p8.native_tm_diagnostics import (
    JsonValue,
    NativeTrafficManagerDiagnosticsCollector,
    NativeTrafficManagerDiagnosticsConfig,
    NativeTrafficManagerDiagnosticsSchemaError,
)
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import DevOutcome, Receipt, RegistrationRequest, TrainingRun
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
    FakeTrafficManagerRuntimeAttestor,
)
from tests.test_p8_native_tm_diagnostics import (
    FakeDiagnosticTrafficManager,
    _attestation,
    _context,
    _observation,
    _open,
    _record,
    _rows,
    _snapshot,
)
from tests.test_p8_operator_backend import (
    EvidenceDriverView,
    RunnerEvidence,
    StrictClock,
    runtime_config,
)


class CleanupFaultManager(FakeDiagnosticTrafficManager):
    def disable_diagnostic_trace(self) -> None:
        self.calls.append(("disable",))
        raise OSError("disable failed")


class CloseFaultStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        raise OSError("stream close failed")


class NativeRecordFaultStream(io.StringIO):
    def write(self, value: str) -> int:
        if '"kind":"native_record"' in value:
            raise OSError("native record write failed")
        return super().write(value)


def test_schema_mismatch_preserves_original_through_all_close_failures(
    tmp_path: Path,
) -> None:
    invalid = _snapshot([])
    invalid["selected_actor"] = 7
    manager = CleanupFaultManager(
        [_snapshot([]), invalid, _snapshot([], enabled=False)]
    )
    stream = CloseFaultStream()
    collector = NativeTrafficManagerDiagnosticsCollector.open(
        manager,
        actor_id=42,
        output_dir=tmp_path,
        attestation=_attestation(tmp_path),
        config=NativeTrafficManagerDiagnosticsConfig(capacity=8),
        context=_context(),
        monotonic_clock=lambda: 0.0,
        stream_factory=lambda _path: stream,
    )

    with pytest.raises(NativeTrafficManagerDiagnosticsSchemaError) as caught:
        collector.close()

    assert caught.value.field == "selected_actor"
    assert manager.calls[-3:] == [("read",), ("disable",), ("read",)]
    assert stream.close_calls == 1
    assert collector.status.coverage_status == "FAILED_OBSERVER"
    notes = getattr(caught.value, "__notes__", ())
    assert any("disable failed" in note for note in notes)
    assert any("stream close failed" in note for note in notes)


def test_validated_loss_is_retained_when_native_record_write_fails(
    tmp_path: Path,
) -> None:
    manager = FakeDiagnosticTrafficManager(
        [
            _snapshot([]),
            _snapshot([_record(1)], overwritten=3, contention_dropped=2),
            _snapshot([], overwritten=3, contention_dropped=2),
            _snapshot([], enabled=False, overwritten=3, contention_dropped=2),
        ]
    )
    stream = NativeRecordFaultStream()
    collector = NativeTrafficManagerDiagnosticsCollector.open(
        manager,
        actor_id=42,
        output_dir=tmp_path,
        attestation=_attestation(tmp_path),
        config=NativeTrafficManagerDiagnosticsConfig(capacity=8, interval_seconds=0.5),
        context=_context(),
        monotonic_clock=iter((0.0, 0.5)).__next__,
        stream_factory=lambda _path: stream,
    )

    with pytest.raises(OSError, match="native record write failed"):
        collector.poll(_observation())

    payload = collector.status.payload()
    assert payload["overwritten"] == 3
    assert payload["contention_dropped"] == 2
    assert payload["coverage_status"] == "FAILED_OBSERVER"
    assert collector._seen == set()


def test_every_jsonl_row_has_actor_and_session_receipt_time(tmp_path: Path) -> None:
    manager = FakeDiagnosticTrafficManager(
        [_snapshot([]), _snapshot([]), _snapshot([], enabled=False)]
    )
    collector = _open(manager, tmp_path)

    collector.close()

    rows = _rows(collector.path)
    assert all(row["ego_actor_id"] == 42 for row in rows)
    assert all(isinstance(row["session_elapsed_s"], float) for row in rows)


class FinalCancellationTrafficManager(FakeTrafficManager):
    def __init__(self, vehicle: FakeResearchVehicle) -> None:
        super().__init__()
        self.vehicle = vehicle
        self.enabled = False
        self.saw_active = False
        self.cancelled = False
        self.read_after_cancel = False

    def enable_diagnostic_trace(self, actor_id: int, capacity: int) -> None:
        self.enabled = True
        self.calls.append(("diagnostic_enable", actor_id, capacity))

    def read_diagnostic_trace(self) -> dict[str, JsonValue]:
        self.saw_active = (
            self.saw_active or (True, 8000) in self.vehicle.autopilot_port_calls
        )
        final_false = self.saw_active and self.vehicle.autopilot_port_calls[-1:] == [
            (False, 8000)
        ]
        if final_false:
            self.cancelled = True
            self.read_after_cancel = True
        records = (
            [
                {
                    "actor_id": self.vehicle.id,
                    "event": "force_cancelled",
                    "reason": "manual_deregistration",
                    "trace_session": 1,
                    "event_sequence": 1,
                    "native_steady_ns": 1,
                }
            ]
            if self.cancelled
            else []
        )
        return {
            "selected_actor": self.vehicle.id,
            "enabled": self.enabled,
            "capacity": 8,
            "trace_session": 1,
            "overwritten": 0,
            "contention_dropped": 0,
            "internal_dropped": 0,
            "records": records,
        }

    def disable_diagnostic_trace(self) -> None:
        assert self.read_after_cancel
        self.enabled = False
        self.calls.append(("diagnostic_disable",))


class PassiveDiagnosticTrafficManager(FakeTrafficManager):
    def __init__(self, vehicle: FakeResearchVehicle) -> None:
        super().__init__()
        self.vehicle = vehicle
        self.enabled = False

    def enable_diagnostic_trace(self, actor_id: int, capacity: int) -> None:
        self.enabled = True

    def read_diagnostic_trace(self) -> dict[str, JsonValue]:
        return {
            "selected_actor": self.vehicle.id,
            "enabled": self.enabled,
            "capacity": 8,
            "trace_session": 1,
            "overwritten": 0,
            "contention_dropped": 0,
            "internal_dropped": 0,
            "records": [],
        }

    def disable_diagnostic_trace(self) -> None:
        self.enabled = False


def test_nominal_module_two_handoff_uses_one_collector_through_final_cancel(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    evidence = RunnerEvidence([], [], [], [])
    vehicle = FakeResearchVehicle()
    run_clock = FakeMonotonicClock()
    world = FakeResearchWorld(vehicle, monotonic_clock=run_clock)
    manager = FinalCancellationTrafficManager(vehicle)
    base_runtime = runtime_config(clock, evidence)

    def build(config, borrowed_log):
        if config.duration != 780.0:
            setup_vehicle = FakeResearchVehicle()
            setup_clock = FakeMonotonicClock()
            setup_world = FakeResearchWorld(setup_vehicle, monotonic_clock=setup_clock)

            def setup_viewer_factory(world_arg, hero, view_config):
                viewer = EvidenceDriverView(view_config, evidence)
                viewer.actions = [lambda: setup_clock.advance(config.duration)]
                evidence.viewers.append(viewer)
                return viewer

            return ResearchNoARunner(
                config,
                client=FakeClient(
                    setup_world, PassiveDiagnosticTrafficManager(setup_vehicle)
                ),
                viewer_factory=setup_viewer_factory,
                monotonic_clock=setup_clock,
                monotonic_ns=clock.monotonic_ns,
                utc_ns=clock.utc_ns,
                borrowed_log=borrowed_log,
                tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
            )
        assert config.duration == 780.0

        def viewer_factory(world_arg, hero, view_config):
            viewer = EvidenceDriverView(view_config, evidence)
            viewer.actions = [lambda: run_clock.advance(0.5)]
            evidence.viewers.append(viewer)
            return viewer

        return ResearchNoARunner(
            config,
            client=FakeClient(world, manager),
            viewer_factory=viewer_factory,
            monotonic_clock=run_clock,
            monotonic_ns=clock.monotonic_ns,
            utc_ns=clock.utc_ns,
            driver_input_source=lambda: DriverInput(activation_requested=True),
            borrowed_log=borrowed_log,
            tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
        )

    runtime = replace(
        base_runtime,
        runner_builder=build,
        native_tm_diagnostics=NativeTrafficManagerDiagnosticsConfig(8, 0.5),
    )
    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime,
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            participant_id="R-DIAG",
            run_id="operator-diag",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=Module2Condition.NOA_L2,
        )
    )
    while controller.snapshot().stage is not Stage.MODULE_2_RUNNING:
        stage = controller.snapshot().stage
        if stage is Stage.CONSENT_AND_ELIGIBILITY:
            controller.record_execution_policy_outcome(
                DevOutcome.SKIPPED, "external consent management"
            )
        elif stage is Stage.TRAINING:
            controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
            controller.record_execution_policy_outcome(
                DevOutcome.PARTIAL, "actual training captured"
            )
        elif stage is Stage.EYE_TRACKER_CALIBRATION:
            controller.record_actual(
                Receipt("calibration_boundary", "EXTERNAL_OPERATOR_STEP", "UNCONNECTED")
            )
        elif stage is Stage.MODULE_1_RUNNING:
            controller.execute_current_module(1.0)
            controller.approve_dev_outcome(DevOutcome.PARTIAL, "bounded module one")
        clock.advance(1_000.0)
        controller.advance()

    result = controller.execute_current_module()

    assert result.requested_duration_s == 780.0
    artifact = next(
        controller.session.services.directory.glob(
            "native_tm_diagnostics.MODULE_2_RUNNING.ego-*.jsonl"
        )
    )
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    records = [row for row in rows if row["kind"] == "native_record"]
    assert {row["measurement_phase"] for row in rows} == {"MODULE_2"}
    assert [row["native_payload"]["event"] for row in records] == ["force_cancelled"]
    assert manager.read_after_cancel
    assert rows[-1]["kind"] == "final_summary"
    controller.abort("offline diagnostic handoff verification complete")
