from __future__ import annotations

from dataclasses import replace

import carla
import pytest

from src.experiment.automation_interaction import AutomationInteractionController
from src.experiment.automation_interaction_types import (
    AutomationInteractionConfig,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
)
from src.experiment.context import ExperimentModule, Module1Condition
from src.scenario.research_noa_control_delivery import make_checked_control_sender
from src.scenario.research_noa_traffic_manager import (
    StaleLaneChangeGenerationError,
    TrafficManagerScheduler,
    build_traffic_manager_noa_runtime,
)
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import (
    FakeTrafficManager,
    FakeTrafficManagerRuntimeAttestor,
)
from tests.test_driver_view import FakeWorld
from tests.test_p8_control_delivery import (
    BatchResponse,
    CacheSeparatedClient,
    CacheSeparatedVehicle,
    control_values,
)


@pytest.mark.parametrize("lateral", [0.0, -.6, .6])
@pytest.mark.parametrize(
    "driver_input,expected,reason",
    [
        (DriverInput(brake=0.2), carla.VehicleControl(brake=0.2), DeactivationReason.DRIVER_BRAKE),
        (DriverInput(throttle=0.4, steering=0.1, steering_engaged=True),
         carla.VehicleControl(throttle=0.4, steer=0.1), DeactivationReason.DRIVER_STEERING),
        (DriverInput(throttle=0.4, deactivation_requested=True),
         carla.VehicleControl(throttle=0.4), DeactivationReason.DRIVER_BUTTON),
    ],
)
def test_takeover_resends_cached_manual_value_after_deregistration(
    driver_input: DriverInput,
    expected: carla.VehicleControl,
    reason: DeactivationReason,
    lateral: float,
) -> None:
    vehicle = CacheSeparatedVehicle()

    class OrderedClient(CacheSeparatedClient):
        def apply_batch_sync(
            self, commands: list[carla.command.ApplyVehicleControl], do_tick: bool = False,
        ) -> list[BatchResponse]:
            vehicle.events.append("manual_batch")
            return super().apply_batch_sync(commands, do_tick)

    client = OrderedClient(vehicle)
    # Keep the map's original lane center; move only the test vehicle.
    vehicle.transform = replace(
        vehicle.transform,
        location=replace(vehicle.transform.location, y=vehicle.transform.location.y + lateral),
    )
    tm = FakeTrafficManager()
    attestor = FakeTrafficManagerRuntimeAttestor()
    bundle = build_traffic_manager_noa_runtime(
        vehicle, client.world.get_map(), tm, world=client.world,
        ownership_verifier=attestor, runtime_attestation=attestor.attestation,
    )
    backend = bundle.traffic_manager_backend
    assert backend is not None
    events = []
    interaction = AutomationInteractionController(
        runtime=bundle.automation_runtime, geometry=bundle.lane_geometry,
        vehicle=vehicle, steering_target=None,
        config=AutomationInteractionConfig(
            0.2, 0.1, 0.05, True, True, require_centered_activation=lateral == 0.0),
        event_sink=events.append,
    )
    interaction.initialize(ExperimentModule.MODULE_1, Module1Condition.NO_SURT)
    viewer = ResearchLiveDriverView(FakeWorld(), vehicle, ResearchDriverViewConfig(
        manual_control_limits=ResearchManualControlLimits(1.0, 0.5, 0.15),
        automation_state_source=lambda: bundle.automation_runtime.state,
        control_sender=make_checked_control_sender(client, vehicle.id),
    ))
    scheduler = TrafficManagerScheduler(bundle.automation_runtime, vehicle)
    vehicle.apply_control(expected)
    viewer._pending_driver_input = driver_input
    viewer._apply_manual_control()
    initial_batches = len(client.calls)
    interaction.update(DriverInput(activation_requested=True))
    old_generation = backend.activation_generation
    backend.submit_lane_change("old-request", old_generation, right_lane_change=True)
    vehicle.traffic_manager_control(carla.VehicleControl(throttle=0.85, steer=-0.2))
    vehicle.get_control()
    viewer._pending_driver_input = driver_input
    assert scheduler.update() is True
    viewer._apply_manual_control()
    assert len(client.calls) == initial_batches
    assert control_values(vehicle.client_cache) == control_values(expected)

    interaction.update(driver_input)
    assert scheduler.update() is False
    viewer._apply_manual_control()

    assert vehicle.events[-2:] == ["autopilot:False", "manual_batch"]
    assert len(client.calls) == initial_batches + 1
    assert control_values(vehicle.get_control()) == control_values(expected)
    assert [event.deactivation_reason for event in events
            if event.event_type is AutomationInteractionEventType.DISENGAGEMENT] == [reason]
    interaction.update(DriverInput(activation_requested=True))
    with pytest.raises(StaleLaneChangeGenerationError):
        backend.submit_lane_change("late-callback", old_generation, right_lane_change=True)
    assert [value for name, value in tm.calls if name == "force_lane_change"] == [True]
    assert all(not do_tick for _, do_tick in client.calls)
