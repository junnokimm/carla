from __future__ import annotations

import pygame
import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction import DriverInput
from src.scenario.research_noa_control_delivery import make_checked_control_sender
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from src.vehicle.driving_mode import DrivingMode
from tests.test_driver_view import FakeWorld
from tests.test_p8_control_delivery import (
    CacheSeparatedClient,
    CacheSeparatedVehicle,
    control_values,
)


def manual_state() -> AutomationState:
    return AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.MANUAL,
    )


def active_state() -> AutomationState:
    return AutomationState(
        AutomationAvailability.AVAILABLE,
        DrivingControlMode.NOA_ACTIVE,
    )


def test_live_manual_writer_batches_repeated_release_priority_and_reset_controls() -> None:
    vehicle = CacheSeparatedVehicle()
    client = CacheSeparatedClient(vehicle)
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            manual_control_limits=ResearchManualControlLimits(0.4, 0.5, 0.15),
            automation_state_source=manual_state,
            control_sender=make_checked_control_sender(client, vehicle.id),
        ),
    )

    for driver_input in (
        DriverInput(throttle=1.0),
        DriverInput(throttle=1.0),
        DriverInput(),
        DriverInput(throttle=1.0, brake=1.0, steering=-1.0, hand_brake=True),
    ):
        viewer._pending_driver_input = driver_input
        viewer._apply_manual_control()
    viewer._reset_manual_control()

    controls = [call[0][0].control for call in client.calls]
    assert [control.throttle for control in controls[:3]] == pytest.approx(
        [0.4, 0.4, 0.0]
    )
    priority = controls[3]
    assert priority.throttle == 0.0
    assert priority.brake == pytest.approx(0.5)
    assert priority.steer == pytest.approx(-0.15)
    assert priority.hand_brake is True
    assert priority.reverse is False
    assert priority.manual_gear_shift is False
    assert priority.gear == 0
    assert control_values(controls[4]) == (0.0, 0.0, 0.0, False)
    assert all(call[1] is False for call in client.calls)


def test_live_manual_writer_and_reset_send_nothing_while_canonical_noa_is_active() -> (
    None
):
    vehicle = CacheSeparatedVehicle()
    client = CacheSeparatedClient(vehicle)
    viewer = ResearchLiveDriverView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            manual_control_limits=ResearchManualControlLimits(0.4, 0.5, 0.15),
            automation_state_source=active_state,
            control_sender=make_checked_control_sender(client, vehicle.id),
        ),
    )
    viewer._pending_driver_input = DriverInput(throttle=1.0)

    viewer._apply_manual_control()
    viewer._reset_manual_control()

    assert client.calls == []


def test_driver_loop_canonical_noa_gate_blocks_manual_batch_when_scheduler_declines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.scenario import driver_view

    class LoopView(ResearchLiveDriverView):
        def _prepare_frame(self) -> None:
            return

        def _draw(self, _screen: pygame.Surface) -> None:
            return

        def _update_turn_signal_audio(self) -> None:
            return

    class DecliningScheduler:
        def update(self) -> bool:
            return False

    class FrameClock:
        def tick(self, _frame_rate: int) -> None:
            return

    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(driver_view.time, "monotonic", times.__next__)
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda _size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda _title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", list)
    monkeypatch.setattr(driver_view.pygame.time, "Clock", FrameClock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    vehicle = CacheSeparatedVehicle()
    client = CacheSeparatedClient(vehicle)
    viewer = LoopView(
        FakeWorld(),
        vehicle,
        ResearchDriverViewConfig(
            initial_driving_mode=DrivingMode.MANUAL,
            manual_control_limits=ResearchManualControlLimits(0.4, 0.5, 0.15),
            automation_state_source=active_state,
            driver_input_source=lambda: DriverInput(throttle=1.0),
            control_sender=make_checked_control_sender(client, vehicle.id),
        ),
    )

    viewer.run(1.0, scheduler=DecliningScheduler())

    assert client.calls == []
