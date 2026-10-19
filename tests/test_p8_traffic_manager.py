from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import carla
import pytest

from src.experiment.assignment import ParticipantAssignment
from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import AutomationInteractionController
from src.experiment.automation_interaction_types import (
    AutomationInteractionConfig,
    AutomationInteractionEvent,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
)
from src.experiment.context import (
    ExperimentModule,
    Module1Condition,
    Module2Condition,
    ParticipantId,
    StudyRunId,
)
from src.p8.native_tm_diagnostics import (
    JsonValue,
    NativeTrafficManagerDiagnosticsConfig,
)
from src.p8.tm_runtime_attestation import RuntimeAttestation
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_traffic_manager import (
    LaneChangeSubmissionStatus,
    StaleLaneChangeGenerationError,
    TrafficManagerNoAControlBackend,
    TrafficManagerScheduler,
    build_traffic_manager_noa_runtime,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
    FakeTrafficManagerRuntimeAttestor,
    make_live_control_config,
)


def test_tm_runtime_configures_safe_defaults_and_registers_only_when_active() -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    traffic_manager = FakeTrafficManager(8123)

    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        world.get_map(),
        traffic_manager,
    )

    assert traffic_manager.calls == [
        ("get_port", 8123),
        ("auto_lane_change", False),
        ("random_left_lanechange_percentage", 0.0),
        ("random_right_lanechange_percentage", 0.0),
        ("update_vehicle_lights", False),
    ]
    assert vehicle.autopilot_port_calls == [(False, 8123)]

    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert traffic_manager.calls[-1] == ("ignore_lights_percentage", 0.0)
    assert vehicle.autopilot_port_calls[-1] == (True, 8123)
    assert vehicle.applied_controls == []


def test_tm_light_policy_is_reasserted_before_each_activation() -> None:
    vehicle = FakeResearchVehicle()
    traffic_manager = FakeTrafficManager(8123)
    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        FakeResearchWorld(vehicle).get_map(),
        traffic_manager,
    )

    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    bundle.automation_runtime.request_control_mode(DrivingControlMode.MANUAL)
    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert traffic_manager.calls.count(("ignore_lights_percentage", 0.0)) == 2
    assert vehicle.events == [
        "autopilot:False",
        "tm:ignore_lights_percentage:0.0",
        "autopilot:True",
        "autopilot:False",
        "tm:ignore_lights_percentage:0.0",
        "autopilot:True",
    ]


def test_tm_scheduler_suppresses_manual_writer_without_custom_control() -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        world.get_map(),
        FakeTrafficManager(),
    )
    scheduler = TrafficManagerScheduler(
        bundle.automation_runtime,
        vehicle,
    )

    assert scheduler.update() is False
    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    assert scheduler.update() is True
    assert vehicle.applied_controls == []
    assert vehicle.get_control_count == 0


def test_tm_backend_exposes_only_transition_capabilities() -> None:
    vehicle = FakeResearchVehicle()
    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        FakeResearchWorld(vehicle).get_map(),
        FakeTrafficManager(),
    )

    assert not hasattr(bundle.control_backend, "step")
    assert not hasattr(bundle.control_backend, "set_driver_steering")


def test_tm_lane_change_is_one_shot_owned_and_empty_rehearsal_only() -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    traffic_manager = FakeTrafficManager(8123)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        traffic_manager,
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()

    submission = backend.submit_lane_change(
        "LC-1",
        backend.activation_generation,
        right_lane_change=True,
    )

    assert submission.request_id == "LC-1"
    assert submission.direction == "RIGHT"
    assert traffic_manager.calls[-1] == ("force_lane_change", True)
    assert attestor.calls[-1] == ("owned", attestor.attestation)
    ongoing = backend.submit_lane_change(
        "LC-2",
        backend.activation_generation,
        right_lane_change=False,
    )
    duplicate = backend.submit_lane_change(
        "LC-1",
        backend.activation_generation,
        right_lane_change=True,
    )
    assert ongoing.status is LaneChangeSubmissionStatus.REJECTED
    assert ongoing.reason == "LANE_CHANGE_ALREADY_IN_FLIGHT"
    assert duplicate.status is LaneChangeSubmissionStatus.REJECTED
    assert duplicate.reason == "REQUEST_ID_ALREADY_CONSUMED"


def test_tm_lane_change_generation_is_invalidated_before_manual_deregistration() -> (
    None
):
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        FakeTrafficManager(8123),
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()
    submission = backend.submit_lane_change(
        "LC-1",
        backend.activation_generation,
        right_lane_change=False,
    )

    backend.enter_manual_control()

    assert backend.lane_change_in_flight is None
    with pytest.raises(StaleLaneChangeGenerationError):
        backend.complete_lane_change(submission.generation)


def test_tm_lane_change_does_not_require_empty_world_rehearsal_inventory() -> None:
    vehicle = FakeResearchVehicle()
    traffic_manager = FakeTrafficManager(8123)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        traffic_manager,
        world=None,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()

    result = backend.submit_lane_change(
        "LC-BLOCKED",
        backend.activation_generation,
        right_lane_change=True,
    )

    assert result.status is LaneChangeSubmissionStatus.SUBMITTED
    assert result.reason is None
    assert traffic_manager.calls[-1] == ("force_lane_change", True)


def test_tm_lane_change_ownership_loss_propagates_without_force() -> None:
    class LostOwnershipAttestor(FakeTrafficManagerRuntimeAttestor):
        def verify_owned(self, _attestation: RuntimeAttestation) -> None:
            raise RuntimeError("listener ownership lost")

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    traffic_manager = FakeTrafficManager(8123)
    attestor = LostOwnershipAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        traffic_manager,
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()

    with pytest.raises(RuntimeError, match="ownership lost"):
        backend.submit_lane_change(
            "LC-LOST",
            backend.activation_generation,
            right_lane_change=True,
        )

    assert all(call[0] != "force_lane_change" for call in traffic_manager.calls)


def test_tm_configuration_failure_attempts_deregistration() -> None:
    class FailingTrafficManager(FakeTrafficManager):
        def auto_lane_change(self, actor: FakeResearchVehicle, enabled: bool) -> None:
            super().auto_lane_change(actor, enabled)
            raise RuntimeError("registration failed")

    vehicle = FakeResearchVehicle()

    with pytest.raises(RuntimeError, match="registration failed"):
        build_traffic_manager_noa_runtime(
            vehicle,
            FakeResearchWorld(vehicle).get_map(),
            FailingTrafficManager(8123),
        )

    assert vehicle.autopilot_port_calls == [(False, 8123)]


def test_tm_activation_failure_rolls_back_remote_registration() -> None:
    class ActivationFailureVehicle(FakeResearchVehicle):
        def set_autopilot(self, enabled: bool, tm_port: int | None = None) -> None:
            super().set_autopilot(enabled, tm_port)
            if enabled:
                raise RuntimeError("activation acknowledgement lost")

    vehicle = ActivationFailureVehicle()
    backend = TrafficManagerNoAControlBackend(vehicle, FakeTrafficManager(8123))

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        backend.enter_noa_control()

    assert vehicle.autopilot_port_calls[-2:] == [(True, 8123), (False, 8123)]


def test_tm_scheduler_observes_changed_speed_limits_with_canonical_state() -> None:
    vehicle = FakeResearchVehicle()
    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        FakeResearchWorld(vehicle).get_map(),
        FakeTrafficManager(),
    )
    observations: list[tuple[DrivingControlMode, float, float]] = []
    scheduler = TrafficManagerScheduler(
        bundle.automation_runtime,
        vehicle,
        speed_limit_sink=lambda state, actual, limit: observations.append(
            (state.control_mode, actual, limit)
        ),
    )

    scheduler.update()
    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    vehicle.velocity = carla.Vector3D(x=72.0 / 3.6)
    vehicle.speed_limit_kmh = 100.0
    scheduler.update()

    assert observations == [
        (DrivingControlMode.MANUAL, 0.0, 80.0),
        (DrivingControlMode.NOA_ACTIVE, 72.0, 100.0),
    ]


def test_tm_activation_failure_deregisters_before_brake_and_destroy() -> None:
    class ActivationFailureVehicle(FakeResearchVehicle):
        def set_autopilot(self, enabled: bool, tm_port: int | None = None) -> None:
            super().set_autopilot(enabled, tm_port)
            if enabled:
                raise RuntimeError("activation acknowledgement lost")

        def destroy(self) -> bool:
            self.events.append("destroy")
            return super().destroy()

    vehicle = ActivationFailureVehicle()
    world = FakeResearchWorld(vehicle)
    interaction = ResearchAutomationInteractionConfig(
        ExperimentModule.MODULE_2,
        Module2Condition.NOA_L2,
        AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
    )
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=replace(
                make_live_control_config(),
                longitudinal=replace(
                    make_live_control_config().longitudinal,
                    target_speed_kmh=None,
                ),
            ),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            automation_interaction=interaction,
            traffic_manager_port=8123,
        ),
        client=FakeClient(world, FakeTrafficManager(8123)),
        viewer_factory=FakeDriverViewFactory(),
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )

    with (
        pytest.raises(RuntimeError, match="acknowledgement lost"),
        runner.session() as session,
    ):
        session.run()

    activation_index = vehicle.events.index("autopilot:True")
    rollback_index = vehicle.events.index("autopilot:False", activation_index)
    brake_index = vehicle.events.index("control:0.5", rollback_index)
    assert (
        activation_index
        < rollback_index
        < brake_index
        < vehicle.events.index("destroy")
    )


def test_tm_report_marks_absent_custom_control_samples_as_none() -> None:
    vehicle = FakeResearchVehicle()
    clock = FakeMonotonicClock()
    world = FakeResearchWorld(vehicle, monotonic_clock=clock)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=replace(
                make_live_control_config(),
                longitudinal=replace(
                    make_live_control_config().longitudinal,
                    target_speed_kmh=None,
                ),
            ),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            traffic_manager_port=8000,
        ),
        client=FakeClient(world, FakeTrafficManager()),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: clock.advance(1.0)]
        report = session.run()

    assert report is not None
    formatted = report.format()
    assert "provider=CARLA_TRAFFIC_MANAGER" in formatted
    assert "control_sample_semantics=NO_CUSTOM_COMMAND_SAMPLES" in formatted
    assert "max_observed_throttle=None" in formatted
    assert "mean_commanded_throttle=None" in formatted
    assert "max_observed_brake=None" in formatted
    assert "max_observed_abs_steering=None" in formatted


def test_successful_tm_run_does_not_repeat_shutdown_during_close() -> None:
    runner, client, _, vehicle = _tm_runner_with_client()

    with runner.session() as session:
        report = session.run()

    assert report is not None
    assert len(client.batch_calls) == 1
    assert vehicle.autopilot_port_calls == [
        (False, 8000),
        (True, 8000),
        (False, 8000),
    ]


def test_native_diagnostics_follow_runner_poll_and_shutdown_order(
    tmp_path: Path,
) -> None:
    class OrderedVehicle(FakeResearchVehicle):
        def destroy(self) -> bool:
            self.events.append("destroy")
            return super().destroy()

    class DiagnosticTrafficManager(FakeTrafficManager):
        def __init__(self, vehicle: OrderedVehicle) -> None:
            super().__init__()
            self.vehicle = vehicle
            self.enabled = False
            self.records = [
                {
                    "actor_id": vehicle.id,
                    "event": "force_accepted",
                    "reason": "accepted",
                    "trace_session": 1,
                    "event_sequence": 1,
                    "native_steady_ns": 1,
                }
            ]

        def enable_diagnostic_trace(self, actor_id: int, capacity: int) -> None:
            self.enabled = True
            self.vehicle.events.append(f"diagnostic:enable:{actor_id}:{capacity}")

        def read_diagnostic_trace(self) -> dict[str, JsonValue]:
            self.vehicle.events.append(f"diagnostic:read:{self.enabled}")
            if (
                self.enabled
                and "autopilot:False" in self.vehicle.events
                and len(self.records) == 1
            ):
                self.records.append(
                    {
                        "actor_id": self.vehicle.id,
                        "event": "force_cancelled",
                        "reason": "manual_deregistration",
                        "trace_session": 1,
                        "event_sequence": 2,
                        "native_steady_ns": 2,
                    }
                )
            return {
                "selected_actor": self.vehicle.id,
                "enabled": self.enabled,
                "capacity": 8,
                "trace_session": 1,
                "overwritten": 0,
                "contention_dropped": 0,
                "internal_dropped": 0,
                "records": self.records,
            }

        def disable_diagnostic_trace(self) -> None:
            self.enabled = False
            self.vehicle.events.append("diagnostic:disable")

    vehicle = OrderedVehicle()
    clock = FakeMonotonicClock()
    world = FakeResearchWorld(vehicle, monotonic_clock=clock)
    manager = DiagnosticTrafficManager(vehicle)
    viewer_factory = FakeDriverViewFactory()
    assignment = ParticipantAssignment(
        ParticipantId("R-DIAG"),
        Module1Condition.NO_SURT,
        Module2Condition.NOA_L2,
        "route",
        "scenario",
        "aoi",
        "program",
    )
    persistence = ResearchPersistenceConfig(
        assignment,
        StudyRunId("run-diag"),
        tmp_path,
        ExperimentModule.MODULE_2,
        "MODULE_2_RUNNING",
    )
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=replace(
                make_live_control_config(),
                longitudinal=replace(
                    make_live_control_config().longitudinal,
                    target_speed_kmh=None,
                ),
            ),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            run_id="run-diag",
            automation_interaction=ResearchAutomationInteractionConfig(
                ExperimentModule.MODULE_2,
                Module2Condition.NOA_L2,
                AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
            ),
            persistence=persistence,
            traffic_manager_port=8000,
            native_tm_diagnostics=NativeTrafficManagerDiagnosticsConfig(8, 0.5),
        ),
        client=FakeClient(world, manager),
        viewer_factory=viewer_factory,
        monotonic_clock=clock,
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = [lambda: clock.advance(0.5)]
        session.run()

    artifact = tmp_path / "native_tm_diagnostics.MODULE_2_RUNNING.ego-42.jsonl"
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    records = [row for row in rows if row["kind"] == "native_record"]
    assert [row["native_payload"]["event_sequence"] for row in records] == [1, 2]
    assert records[-1]["native_payload"]["event"] == "force_cancelled"
    assert runner.native_tm_diagnostics_status is not None
    assert session.native_tm_collector is not None
    assert session.native_tm_collector.status.coverage_status == "COMPLETE"
    assert vehicle.events.index("diagnostic:enable:42:8") < vehicle.events.index(
        "autopilot:False"
    )
    assert vehicle.events.index("diagnostic:disable") < vehicle.events.index("destroy")
    assert rows[-1]["kind"] == "final_summary"


def test_failed_tm_shutdown_is_retried_during_close() -> None:
    runner, client, _, _ = _tm_runner_with_client()
    client.batch_errors.append(RuntimeError("injected shutdown delivery failure"))

    with (
        pytest.raises(RuntimeError, match="injected shutdown delivery failure"),
        runner.session() as session,
    ):
        session.run()

    assert len(client.batch_calls) == 1


def _tm_runner_with_client() -> tuple[
    ResearchNoARunner,
    FakeClient,
    FakeResearchWorld,
    FakeResearchVehicle,
]:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    client = FakeClient(world, FakeTrafficManager())
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=replace(
                make_live_control_config(),
                longitudinal=replace(
                    make_live_control_config().longitudinal,
                    target_speed_kmh=None,
                ),
            ),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            traffic_manager_port=8000,
        ),
        client=client,
        viewer_factory=viewer_factory,
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )
    return runner, client, world, vehicle


def test_tm_runner_spawns_sticky_control_vehicle() -> None:
    runner, _, world, _ = _tm_runner_with_client()

    with runner.session():
        assert ("sticky_control", "true") in world.blueprint.attributes


def test_tm_runner_delivers_shutdown_brake_through_reused_client_batch() -> None:
    runner, client, _, vehicle = _tm_runner_with_client()

    with runner.session():
        pass

    assert len(client.batch_calls) == 1
    commands, do_tick = client.batch_calls[0]
    assert do_tick is False
    assert len(commands) == 1
    assert commands[0].actor_id == vehicle.id
    assert commands[0].control.brake == pytest.approx(0.5)


def test_driver_steering_deregisters_tm_before_manual_control_write() -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        world.get_map(),
        FakeTrafficManager(),
    )
    events: list[AutomationInteractionEvent] = []
    interaction = AutomationInteractionController(
        runtime=bundle.automation_runtime,
        geometry=bundle.lane_geometry,
        vehicle=vehicle,
        steering_target=bundle.steering_target,
        config=AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
        event_sink=events.append,
    )
    interaction.initialize(ExperimentModule.MODULE_1, Module1Condition.NO_SURT)
    interaction.update(DriverInput(activation_requested=True))

    interaction.update(DriverInput(steering_engaged=True, steering=0.3))
    vehicle.apply_control(carla.VehicleControl(steer=0.3))

    assert vehicle.autopilot_port_calls[-1] == (False, 8000)
    assert vehicle.events[-2:] == ["autopilot:False", "control:0.0"]
    steering_events = [
        event.event_type
        for event in events
        if event.deactivation_reason is DeactivationReason.DRIVER_STEERING
    ]
    assert steering_events == [
        AutomationInteractionEventType.TRANSITION,
        AutomationInteractionEventType.DISENGAGEMENT,
    ]
    assert steering_events.count(AutomationInteractionEventType.DISENGAGEMENT) == 1


def test_tm_scheduler_rejects_nonfinite_speed_but_not_speed_above_110() -> None:
    vehicle = FakeResearchVehicle()
    bundle = build_traffic_manager_noa_runtime(
        vehicle,
        FakeResearchWorld(vehicle).get_map(),
        FakeTrafficManager(),
    )
    scheduler = TrafficManagerScheduler(bundle.automation_runtime, vehicle)
    bundle.automation_runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    vehicle.velocity = carla.Vector3D(x=120.0 / 3.6)

    assert scheduler.update() is True

    vehicle.velocity = carla.Vector3D(x=float("nan"))
    with pytest.raises(RuntimeError, match="finite"):
        scheduler.update()
