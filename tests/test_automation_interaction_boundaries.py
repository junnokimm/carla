from __future__ import annotations

from math import nan

import pytest

from src.experiment.automation import AutomationAvailability, DrivingControlMode
from src.experiment.automation_interaction import (
    ActivationFailureReason,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
)
from src.experiment.context import ExperimentModule, Module1Condition, Module2Condition
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryAdapter
from tests.test_automation_interaction import (
    FakeBoundingBox,
    FakeExtent,
    FakeRotation,
    geometry,
    make_controller,
)
from tests.test_carla_lane_geometry import (
    FakeLocation as AdapterLocation,
)
from tests.test_carla_lane_geometry import (
    FakeMap as AdapterMap,
)
from tests.test_carla_lane_geometry import (
    FakeRotation as AdapterRotation,
)
from tests.test_carla_lane_geometry import (
    FakeTransform as AdapterTransform,
)
from tests.test_carla_lane_geometry import (
    FakeVehicle as AdapterVehicle,
)
from tests.test_carla_lane_geometry import (
    make_waypoint,
)


def test_manual_activation_with_held_brake_fails_before_activation() -> None:
    controller, backend, events = make_controller()

    controller.update(
        DriverInput(
            throttle=1.0,
            brake=0.05,
            activation_requested=True,
            stage="brake-held",
        )
    )

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == []
    assert [event.event_type for event in events[-3:]] == [
        AutomationInteractionEventType.REQUEST,
        AutomationInteractionEventType.AVAILABILITY,
        AutomationInteractionEventType.FAILURE,
    ]
    assert events[-1].failure_reason is ActivationFailureReason.DRIVER_BRAKE_ACTIVE


def test_module_two_manual_starts_available_and_manual() -> None:
    controller, backend, events = make_controller()

    state = controller.initialize(ExperimentModule.MODULE_2, Module2Condition.MANUAL)

    assert state.availability is AutomationAvailability.AVAILABLE
    assert state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == []
    assert events[-1].event_type is AutomationInteractionEventType.INITIAL_STATE


def test_initial_state_is_emitted_after_module_two_noa_activation() -> None:
    controller, _, events = make_controller()

    controller.initialize(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)

    assert [event.event_type for event in events] == [
        AutomationInteractionEventType.REQUEST,
        AutomationInteractionEventType.TRANSITION,
        AutomationInteractionEventType.INITIAL_STATE,
    ]
    assert events[-1].state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_initial_state_is_emitted_after_failed_module_two_noa_activation() -> None:
    controller, _, events = make_controller(context=geometry(lateral=0.3))

    controller.initialize(ExperimentModule.MODULE_2, Module2Condition.NOA_L2)

    assert [event.event_type for event in events[-4:]] == [
        AutomationInteractionEventType.REQUEST,
        AutomationInteractionEventType.AVAILABILITY,
        AutomationInteractionEventType.FAILURE,
        AutomationInteractionEventType.INITIAL_STATE,
    ]
    assert events[-1].state.control_mode is DrivingControlMode.MANUAL
    assert events[-2].failure_reason is ActivationFailureReason.NOT_CENTERED


def test_manual_lane_change_updates_technical_availability_with_reason() -> None:
    controller, _, events = make_controller()
    controller.initialize(ExperimentModule.MODULE_1, Module1Condition.NO_SURT)

    controller.update(DriverInput(lane_change_in_progress=True, stage="lane-change"))
    controller.after_control_applied()

    assert controller.state.availability is AutomationAvailability.UNAVAILABLE
    assert events[-1].event_type is AutomationInteractionEventType.AVAILABILITY
    assert events[-1].failure_reason is (
        ActivationFailureReason.LANE_CHANGE_IN_PROGRESS
    )


def test_manual_safe_input_restores_technical_availability() -> None:
    controller, _, events = make_controller()
    controller.update(DriverInput(lane_change_in_progress=True))
    controller.after_control_applied()

    controller.update(DriverInput(stage="safe-again"))
    controller.after_control_applied()

    assert controller.state.availability is AutomationAvailability.AVAILABLE
    assert events[-1].event_type is AutomationInteractionEventType.AVAILABILITY
    assert events[-1].failure_reason is None


def test_active_lane_offset_does_not_auto_disengage() -> None:
    controller, backend, events = make_controller()
    controller.update(DriverInput(activation_requested=True))
    controller.geometry.context = geometry(lateral=0.8)
    event_count = len(events)

    controller.update(DriverInput())

    assert controller.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.calls == ["noa"]
    assert len(events) == event_count


@pytest.mark.parametrize(
    ("context", "expected_reason"),
    [
        (geometry(lateral=0.3), ActivationFailureReason.NOT_CENTERED),
        (geometry(heading=0.2), ActivationFailureReason.HEADING_MISALIGNED),
        (geometry(lateral=0.9), ActivationFailureReason.VEHICLE_STRADDLING),
    ],
)
def test_manual_geometry_gate_updates_availability_reason(
    context, expected_reason: ActivationFailureReason
) -> None:
    controller, _, events = make_controller(context=context)

    controller.update(DriverInput(stage="geometry-gate"))
    controller.after_control_applied()

    assert controller.state.availability is AutomationAvailability.UNAVAILABLE
    assert events[-1].event_type is AutomationInteractionEventType.AVAILABILITY
    assert events[-1].failure_reason is expected_reason


@pytest.mark.parametrize(
    "bounding_box",
    [
        FakeBoundingBox(location=FakeExtent(0.0, 0.9)),
        FakeBoundingBox(rotation=FakeRotation(90.0)),
    ],
)
def test_bounding_box_offset_and_rotation_are_included_in_straddle_gate(
    bounding_box: FakeBoundingBox,
) -> None:
    controller, _, events = make_controller()
    object.__setattr__(controller._vehicle, "bounding_box", bounding_box)

    controller.update(DriverInput(activation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert events[-1].failure_reason is ActivationFailureReason.VEHICLE_STRADDLING


@pytest.mark.parametrize(
    ("context", "extent_field"),
    [
        (geometry(lane_width=nan), None),
        (geometry(), "x"),
        (geometry(), "y"),
    ],
)
def test_nonfinite_lane_or_bounding_box_geometry_fails_closed(
    context, extent_field: str | None
) -> None:
    controller, _, events = make_controller(context=context)
    if extent_field is not None:
        object.__setattr__(controller._vehicle.bounding_box.extent, extent_field, nan)

    controller.update(DriverInput(activation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert events[-1].failure_reason is ActivationFailureReason.INVALID_GEOMETRY


def test_real_adapter_nonfinite_pose_maps_to_invalid_geometry() -> None:
    controller, _, events = make_controller()
    vehicle = AdapterVehicle(
        AdapterTransform(
            AdapterLocation(nan, 0.0, 0.0),
            AdapterRotation(0.0),
        )
    )
    object.__setattr__(vehicle, "bounding_box", controller._vehicle.bounding_box)
    adapter = CarlaLaneGeometryAdapter(AdapterMap(make_waypoint()))
    object.__setattr__(controller._gate, "geometry", adapter)
    object.__setattr__(controller._gate, "vehicle", vehicle)

    controller.update(DriverInput(activation_requested=True))

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert events[-1].failure_reason is ActivationFailureReason.INVALID_GEOMETRY


def test_brake_disengagement_records_transition_then_disengagement() -> None:
    controller, _, events = make_controller()
    controller.update(DriverInput(activation_requested=True))

    controller.update(DriverInput(brake=0.05, stage="brake"))
    controller.after_control_applied()

    assert [event.event_type for event in events[-3:]] == [
        AutomationInteractionEventType.TRANSITION,
        AutomationInteractionEventType.DISENGAGEMENT,
        AutomationInteractionEventType.AVAILABILITY,
    ]
    assert all(
        event.deactivation_reason is DeactivationReason.DRIVER_BRAKE
        for event in events[-3:-1]
    )
    assert events[-1].failure_reason is ActivationFailureReason.DRIVER_BRAKE_ACTIVE


def test_external_unavailability_deactivates_with_explicit_reason() -> None:
    controller, backend, events = make_controller()
    controller.update(DriverInput(activation_requested=True))

    controller.set_external_availability(
        AutomationAvailability.UNAVAILABLE,
        reason=DeactivationReason.AVAILABILITY_LOST,
        stage="safety",
    )

    assert controller.state.control_mode is DrivingControlMode.MANUAL
    assert controller.state.availability is AutomationAvailability.UNAVAILABLE
    assert backend.calls == ["noa", "manual"]
    assert events[-1].deactivation_reason is DeactivationReason.AVAILABILITY_LOST
