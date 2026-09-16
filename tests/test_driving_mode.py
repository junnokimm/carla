from __future__ import annotations


class FakeVehicle:
    def __init__(self) -> None:
        self.autopilot_calls: list[bool] = []

    def set_autopilot(self, enabled: bool) -> None:
        self.autopilot_calls.append(enabled)


def test_controller_starts_autonomous_by_default() -> None:
    from src.vehicle.driving_mode import DrivingMode, DrivingModeController

    vehicle = FakeVehicle()

    controller = DrivingModeController(vehicle)

    assert controller.mode is DrivingMode.AUTONOMOUS
    assert vehicle.autopilot_calls == [True]


def test_toggle_changes_autonomous_to_manual_and_disables_autopilot() -> None:
    from src.vehicle.driving_mode import DrivingMode, DrivingModeController

    vehicle = FakeVehicle()
    controller = DrivingModeController(vehicle)

    mode = controller.toggle()

    assert mode is DrivingMode.MANUAL
    assert controller.mode is DrivingMode.MANUAL
    assert vehicle.autopilot_calls == [True, False]


def test_toggle_changes_manual_to_autonomous_and_enables_autopilot() -> None:
    from src.vehicle.driving_mode import DrivingMode, DrivingModeController

    vehicle = FakeVehicle()
    controller = DrivingModeController(vehicle, initial_mode=DrivingMode.MANUAL)

    mode = controller.toggle()

    assert mode is DrivingMode.AUTONOMOUS
    assert vehicle.autopilot_calls == [False, True]


def test_repeated_toggle_returns_to_original_mode() -> None:
    from src.vehicle.driving_mode import DrivingMode, DrivingModeController

    vehicle = FakeVehicle()
    controller = DrivingModeController(vehicle)

    controller.toggle()
    controller.toggle()

    assert controller.mode is DrivingMode.AUTONOMOUS
    assert vehicle.autopilot_calls == [True, False, True]
