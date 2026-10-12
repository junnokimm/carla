from __future__ import annotations

from dataclasses import replace

import carla
import pytest

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
)
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_traffic_manager import (
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
