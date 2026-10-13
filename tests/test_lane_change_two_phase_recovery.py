from __future__ import annotations

import carla
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.lane_change import LaneChangeDirection
from src.scenario.lane_change_execution import StaleLaneChangeGenerationError
from src.scenario.research_noa_traffic_manager import TrafficManagerNoAControlBackend
from tests.research_noa_fakes import (
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManager,
    FakeTrafficManagerRuntimeAttestor,
)
from tests.test_lane_change_execution_lifecycle import Interaction, make_rig


class DeregistrationFailureVehicle(FakeResearchVehicle):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_deregistration = False

    def set_autopilot(self, enabled: bool, tm_port: int | None = None) -> None:
        super().set_autopilot(enabled, tm_port)
        if not enabled and self.fail_next_deregistration:
            self.fail_next_deregistration = False
            raise RuntimeError("deregistration acknowledgement lost")


class UnverifiedForceTrafficManager(FakeTrafficManager):
    def force_lane_change(
        self,
        actor: FakeResearchVehicle,
        direction: bool,
    ) -> None:
        super().force_lane_change(actor, direction)
        raise RuntimeError("force acknowledgement lost")


@pytest.mark.parametrize(
    "manager_type",
    [FakeTrafficManager, UnverifiedForceTrafficManager],
)
def test_failed_deregistration_retains_native_lock_until_acknowledged_retry(
    manager_type: type[FakeTrafficManager],
) -> None:
    vehicle = DeregistrationFailureVehicle()
    world = FakeResearchWorld(vehicle)
    manager = manager_type(8123)
    attestor = FakeTrafficManagerRuntimeAttestor()
    backend = TrafficManagerNoAControlBackend(
        vehicle,
        manager,
        world=world,
        ownership_verifier=attestor,
        runtime_attestation=attestor.attestation,
    )
    backend.enter_noa_control()
    generation = backend.activation_generation
    original = backend.submit_lane_change(
        "uncertain-old",
        generation,
        right_lane_change=True,
    )
    vehicle.fail_next_deregistration = True

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        backend.enter_manual_control()

    assert backend.active is True
    assert backend.lane_change_in_flight == original
    backend.interrupt_lane_change()
    assert backend.lane_change_in_flight == original
    with pytest.raises(StaleLaneChangeGenerationError):
        backend.submit_lane_change(
            "blocked-before-ack",
            generation,
            right_lane_change=False,
        )
    assert manager.calls.count(("force_lane_change", True)) == 1

    backend.enter_manual_control()
    assert backend.active is False
    assert backend.lane_change_in_flight is None
    backend.enter_noa_control()
    fresh = backend.submit_lane_change(
        "fresh-after-ack",
        backend.activation_generation,
        right_lane_change=False,
    )

    assert fresh.request_id == "fresh-after-ack"
    assert manager.calls.count(("force_lane_change", True)) == 1
    assert manager.calls.count(("force_lane_change", False)) == 1


def test_tracker_clears_only_after_backend_ack_then_fresh_direction_submits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = make_rig(LaneChangeDirection.RIGHT)
    interaction = rig.observer._interaction
    assert isinstance(interaction, Interaction)
    vehicle = rig.vehicle
    failure_pending = True
    original_set_autopilot = vehicle.set_autopilot

    def fail_once(enabled: bool, tm_port: int | None = None) -> None:
        nonlocal failure_pending
        original_set_autopilot(enabled, tm_port)
        if not enabled and failure_pending:
            failure_pending = False
            raise RuntimeError("deregistration acknowledgement lost")

    monkeypatch.setattr(vehicle, "set_autopilot", fail_once)

    def update_with_backend(driver_input: DriverInput) -> AutomationState:
        if driver_input.brake >= 0.05:
            rig.backend.enter_manual_control()
            interaction._state = AutomationState(
                AutomationAvailability.AVAILABLE,
                DrivingControlMode.MANUAL,
            )
        return interaction.state

    monkeypatch.setattr(interaction, "update", update_with_backend)

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        rig.observer.update(DriverInput(brake=0.1))

    assert rig.observer.lane_change_in_progress is True
    assert rig.backend.lane_change_in_flight is not None
    rig.observer.finish_lane_change_observation(
        "ERROR", rig.persistence.source.get_host_timestamp()
    )
    assert rig.observer.lane_change_in_progress is True
    assert rig.backend.lane_change_in_flight is not None

    rig.observer.update(DriverInput(brake=0.1))
    assert rig.observer.lane_change_in_progress is False
    assert rig.backend.lane_change_in_flight is None

    rig.backend.enter_noa_control()
    interaction._state = AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )
    vehicle.current_light_state = carla.VehicleLightState.LeftBlinker
    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))

    forces = [value for name, value in rig.traffic_manager.calls if name == "force_lane_change"]
    assert forces == [True, False]
