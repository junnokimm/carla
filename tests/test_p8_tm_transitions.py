from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import carla
import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import AutomationInteractionController
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    AutomationInteractionConfig,
    AutomationInteractionEvent,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
)
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.scenario.research_noa_traffic_manager import (
    TrafficManagerScheduler,
    build_traffic_manager_noa_runtime,
)
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import (
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
)
from tests.test_driver_view import FakeWorld


class SignalVehicle(FakeResearchVehicle):
    def set_light_state(self, state: carla.VehicleLightState) -> None:
        self.current_light_state = state


@dataclass(frozen=True, slots=True)
class Rig:
    vehicle: SignalVehicle
    interaction: AutomationInteractionController
    scheduler: TrafficManagerScheduler
    viewer: ResearchLiveDriverView
    events: list[AutomationInteractionEvent]


@pytest.fixture
def rig() -> Rig:
    vehicle = SignalVehicle()
    bundle = build_traffic_manager_noa_runtime(
        vehicle, FakeResearchWorld(vehicle).get_map(), FakeTrafficManager(8123)
    )
    events: list[AutomationInteractionEvent] = []
    interaction = AutomationInteractionController(
        runtime=bundle.automation_runtime,
        geometry=bundle.lane_geometry,
        vehicle=vehicle,
        steering_target=None,
        config=AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
        event_sink=events.append,
    )
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            automation_state_source=lambda: bundle.automation_runtime.state,
            manual_control_limits=ResearchManualControlLimits(1.0, 0.5, 0.15),
            traffic_manager_port=8123,
        ),
    )
    return Rig(
        vehicle,
        interaction,
        TrafficManagerScheduler(bundle.automation_runtime, vehicle),
        viewer,
        events,
    )


@pytest.mark.parametrize(
    ("driver_input", "reason"),
    [
        (DriverInput(brake=0.05), DeactivationReason.DRIVER_BRAKE),
        (
            DriverInput(steering_engaged=True, steering=0.1),
            DeactivationReason.DRIVER_STEERING,
        ),
        (DriverInput(deactivation_requested=True), DeactivationReason.DRIVER_BUTTON),
    ],
)
def test_tm_driver_takeover_precedes_real_view_manual_write(
    rig: Rig, driver_input: DriverInput, reason: DeactivationReason
) -> None:
    # Given: active TM with no local vehicle-control writer.
    rig.interaction.initialize(ExperimentModule.MODULE_1, Module1Condition.NO_SURT)
    rig.interaction.update(DriverInput(activation_requested=True))
    assert rig.scheduler.update() is True
    assert rig.vehicle.applied_controls == []

    # When: run the same input -> scheduler -> manual-write order as DriverView.
    rig.interaction.update(driver_input)
    rig.viewer._pending_driver_input = driver_input
    if not rig.scheduler.update():
        rig.viewer._apply_manual_control()
    rig.interaction.after_control_applied()

    # Then: one distinct disengagement, deregistration before the driver command.
    assert rig.interaction.state.control_mode is DrivingControlMode.MANUAL
    assert rig.vehicle.events[-2] == "autopilot:False"
    command = rig.vehicle.applied_controls[-1]
    assert command.brake == pytest.approx(driver_input.brake)
    assert command.steer == pytest.approx(driver_input.steering)
    assert [
        event.deactivation_reason
        for event in rig.events
        if event.event_type is AutomationInteractionEventType.DISENGAGEMENT
    ] == [reason]


def test_tm_reactivation_rechecks_gate_without_lifetime_limit(rig: Rig) -> None:
    # Given: a fresh voluntary-use module.
    rig.interaction.initialize(ExperimentModule.MODULE_1, Module1Condition.NO_SURT)

    # When: repeatedly reject an unsafe request, then activate and disengage.
    for _ in range(30):
        rig.interaction.update(
            DriverInput(activation_requested=True, lane_change_in_progress=True)
        )
        assert rig.interaction.state.control_mode is DrivingControlMode.MANUAL
        rig.interaction.update(DriverInput(activation_requested=True))
        assert rig.scheduler.update() is True
        rig.interaction.update(DriverInput(deactivation_requested=True))

    # Then: every safe request succeeds exactly once and every failed gate is logged.
    assert sum(enabled for enabled, _ in rig.vehicle.autopilot_port_calls) == 30
    assert sum(
        event.event_type is AutomationInteractionEventType.FAILURE
        and event.failure_reason is ActivationFailureReason.LANE_CHANGE_IN_PROGRESS
        for event in rig.events
    ) == 30
    assert rig.vehicle.applied_controls == []


@pytest.mark.parametrize("key", [pygame.K_z, pygame.K_x])
def test_tm_active_indicator_toggle_is_not_steering_or_disengagement(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, key: int
) -> None:
    # Given: automatic steering readback is nonzero, driver has no steering input.
    rig.interaction.initialize(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)
    rig.vehicle.current_control.steer = 0.7
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    events = [pygame.event.Event(pygame.KEYDOWN, key=key)]

    # When: the actual research input collector and signal toggle handle Z or X.
    driver_input = rig.viewer._collect_driver_input(events)
    rig.interaction.update(driver_input)
    rig.viewer._handle_mode_events(events)

    # Then: TM retains authority, but the driver's selected indicator is on.
    expected = (
        carla.VehicleLightState.LeftBlinker
        if key == pygame.K_z
        else carla.VehicleLightState.RightBlinker
    )
    assert rig.vehicle.get_light_state() & expected
    assert not driver_input.steering_engaged
    assert not driver_input.deactivation_requested
    assert rig.interaction.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert rig.vehicle.applied_controls == []


@pytest.mark.parametrize(
    ("module", "condition", "active"),
    [
        (ExperimentModule.MODULE_1, Module1Condition.NO_SURT, False),
        (ExperimentModule.MODULE_2, Module2Condition.MANUAL, False),
        (ExperimentModule.MODULE_2, Module2Condition.NOA_L2, True),
    ],
)
def test_tm_initial_mode_is_actual_registration_at_standstill(
    rig: Rig,
    module: ExperimentModule,
    condition: Module1Condition | Module2Condition,
    active: bool,
) -> None:
    # Given: zero measured speed, not a synthetic launch speed.
    rig.vehicle.velocity = carla.Vector3D()

    # When: initialize the assigned module.
    rig.interaction.initialize(module, condition)

    # Then: registration, not a PI target, follows the assigned initial state.
    assert rig.vehicle.autopilot_port_calls[-1] == (active, 8123)
    assert rig.scheduler.update() is active
    assert rig.vehicle.applied_controls == []
