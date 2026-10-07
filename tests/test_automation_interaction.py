from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction import (
    ActivationFailureReason,
    AutomationInteractionConfig,
    AutomationInteractionController,
    AutomationInteractionEvent,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.context import (
    ExperimentModule,
    Module1Condition,
    Module2Condition,
)
from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.vehicle.carla_lane_geometry import (
    CarlaLaneGeometryContext,
    CarlaLaneGeometryUnavailableError,
)


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.steering_overrides: list[float | None] = []

    def enter_manual_control(self) -> None:
        self.calls.append("manual")

    def enter_noa_control(self) -> None:
        self.calls.append("noa")

    def set_driver_steering(self, steering: float | None) -> None:
        self.steering_overrides.append(steering)


@dataclass(frozen=True, slots=True)
class FakeExtent:
    x: float = 2.0
    y: float = 0.9


@dataclass(frozen=True, slots=True)
class FakeRotation:
    yaw: float = 0.0


@dataclass(frozen=True, slots=True)
class FakeBoundingBox:
    extent: FakeExtent = field(default_factory=FakeExtent)
    location: FakeExtent = field(default_factory=lambda: FakeExtent(0.0, 0.0))
    rotation: FakeRotation = field(default_factory=FakeRotation)


@dataclass(frozen=True, slots=True)
class FakeVehicle:
    bounding_box: FakeBoundingBox = field(default_factory=FakeBoundingBox)


class FakeGeometry:
    def __init__(self, context: CarlaLaneGeometryContext | None) -> None:
        self.context = context

    def observe_with_context(self, vehicle: FakeVehicle) -> CarlaLaneGeometryContext:
        if self.context is None:
            raise CarlaLaneGeometryUnavailableError("driving lane waypoint")
        return self.context


def geometry(
    *, lateral: float = 0.0, heading: float = 0.0, lane_width: float = 3.5
) -> CarlaLaneGeometryContext:
    return CarlaLaneGeometryContext(
        geometry=LaneGeometryObservation(lateral, heading),
        road_id=1,
        section_id=0,
        lane_id=-2,
        lane_width_m=lane_width,
        vehicle_pose=PlanarPose(0.0, lateral, heading),
        waypoint_pose=PlanarPose(0.0, 0.0, 0.0),
    )


def make_controller(
    *,
    context: CarlaLaneGeometryContext | None = None,
    available: bool = True,
    button_deactivation: bool = True,
) -> tuple[
    AutomationInteractionController,
    RecordingBackend,
    list[AutomationInteractionEvent],
]:
    backend = RecordingBackend()
    runtime = AutomationRuntimeController(
        AutomationState(
            AutomationAvailability.AVAILABLE
            if available
            else AutomationAvailability.UNAVAILABLE,
            DrivingControlMode.MANUAL,
        ),
        backend,
    )
    events: list[AutomationInteractionEvent] = []
    controller = AutomationInteractionController(
        runtime=runtime,
        geometry=FakeGeometry(context or geometry()),
        vehicle=FakeVehicle(),
        steering_target=backend,
        config=AutomationInteractionConfig(
            center_tolerance_m=0.2,
            heading_tolerance_rad=0.1,
            driver_brake_threshold=0.05,
            button_deactivation_enabled=button_deactivation,
        ),
        event_sink=events.append,
    )
    return controller, backend, events


@pytest.mark.parametrize("condition", list(Module1Condition))
def test_module_one_starts_available_and_manual(condition: Module1Condition) -> None:
    controller, backend, _ = make_controller()

    state = controller.initialize(ExperimentModule.MODULE_1, condition)

    assert state == AutomationState(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    assert backend.calls == []


def test_module_two_manual_starts_available_and_manual() -> None:
    controller, backend, _ = make_controller()

    state = controller.initialize(ExperimentModule.MODULE_2, Module2Condition.MANUAL)

    assert state == AutomationState(
        AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL
    )
    assert backend.calls == []


def test_module_two_noa_starts_active_only_after_safe_gate() -> None:
    controller, backend, _ = make_controller()

    state = controller.initialize(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)

    assert state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.calls == ["noa"]


def test_module_two_noa_unsafe_alignment_fails_closed() -> None:
    controller, backend, events = make_controller(context=geometry(lateral=0.3))

    state = controller.initialize(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)

    assert state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == []
    assert events[-2].failure_reason is ActivationFailureReason.NOT_CENTERED


def test_centered_driver_request_activates() -> None:
    controller, backend, _ = make_controller()

    controller.update(DriverInput(activation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.calls == ["noa"]


def test_activation_with_engaged_steering_applies_override_in_same_update() -> None:
    controller, backend, _ = make_controller()

    controller.update(
        DriverInput(
            activation_requested=True,
            steering=-0.8,
            steering_engaged=True,
        )
    )

    assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.steering_overrides == [-0.8]


def test_whole_vehicle_straddle_rejects_activation() -> None:
    controller, backend, events = make_controller(context=geometry(lateral=0.9))

    controller.update(DriverInput(activation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == []
    assert events[-1].failure_reason is ActivationFailureReason.VEHICLE_STRADDLING


def test_lane_change_input_rejects_activation() -> None:
    controller, _, events = make_controller()

    controller.update(
        DriverInput(activation_requested=True, lane_change_in_progress=True)
    )

    assert events[-1].failure_reason is ActivationFailureReason.LANE_CHANGE_IN_PROGRESS


def test_unavailable_state_rejects_activation() -> None:
    controller, backend, events = make_controller(available=False)

    controller.update(DriverInput(activation_requested=True))

    assert backend.calls == []
    assert events[-1].failure_reason is ActivationFailureReason.UNAVAILABLE


def test_geometry_failure_rejects_activation_with_explicit_reason() -> None:
    controller, _, events = make_controller()
    controller.geometry.context = None

    controller.update(DriverInput(activation_requested=True))

    assert events[-1].failure_reason is ActivationFailureReason.GEOMETRY_UNAVAILABLE


def test_brake_disengages_before_forwarding_manual_input() -> None:
    controller, backend, events = make_controller()
    controller.update(DriverInput(activation_requested=True))

    controller.update(DriverInput(brake=0.05, steering=0.4, steering_engaged=True))

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == ["noa", "manual"]
    assert backend.steering_overrides[-1] is None
    assert events[-2].deactivation_reason is DeactivationReason.DRIVER_BRAKE


def test_button_deactivation_has_distinct_reason_when_enabled() -> None:
    controller, _, events = make_controller()
    controller.update(DriverInput(activation_requested=True))

    controller.update(DriverInput(deactivation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert events[-1].deactivation_reason is DeactivationReason.DRIVER_BUTTON


def test_button_deactivation_is_ignored_when_disabled() -> None:
    controller, _, events = make_controller(button_deactivation=False)
    controller.update(DriverInput(activation_requested=True))
    event_count = len(events)

    controller.update(DriverInput(deactivation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert len(events) == event_count


def test_driver_steering_has_priority_without_mode_drop() -> None:
    controller, backend, _ = make_controller()
    controller.update(DriverInput(activation_requested=True))

    controller.update(DriverInput(steering=-0.8, steering_engaged=True))

    assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.steering_overrides[-1] == -0.8


def test_reactivation_uses_same_gate_and_records_request_then_transition() -> None:
    controller, backend, events = make_controller()
    controller.update(DriverInput(activation_requested=True))
    controller.update(DriverInput(brake=1.0))

    controller.update(DriverInput(activation_requested=True))

    assert backend.calls == ["noa", "manual", "noa"]
    assert [event.event_type for event in events[-2:]] == [
        AutomationInteractionEventType.REQUEST,
        AutomationInteractionEventType.TRANSITION,
    ]


@pytest.mark.parametrize(
    "driver_input",
    [
        DriverInput(throttle=1.0, brake=1.0, steering=-1.0),
        DriverInput(throttle=0.0, brake=0.0, steering=1.0),
    ],
)
def test_driver_input_accepts_only_bounded_normalized_controls(
    driver_input: DriverInput,
) -> None:
    assert 0.0 <= driver_input.throttle <= 1.0
    assert 0.0 <= driver_input.brake <= 1.0
    assert -1.0 <= driver_input.steering <= 1.0


def test_driver_input_rejects_out_of_range_control() -> None:
    with pytest.raises(ValueError):
        DriverInput(steering=1.01)
