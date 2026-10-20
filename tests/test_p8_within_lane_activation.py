"""Activation uses the whole lane; ordinary lane changes keep strict alignment."""

from dataclasses import asdict, replace
from math import cos, radians, sin

import carla
import pytest

from src.experiment.automation import AutomationAvailability, AutomationState, DrivingControlMode
from src.experiment.automation_activation_gate import AutomationActivationGate, lane_alignment_failure
from src.experiment.automation_interaction import AutomationInteractionController, DriverInput
from src.experiment.automation_interaction_types import ActivationFailureReason, DeactivationReason
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.context import ExperimentModule, ExperimentPhase, Module1Condition, Module2Condition
from src.experiment.lane_change import LaneChangeDirection
from src.p8.model import Stage
from src.p8.operator_cli import _runtime_config, build_parser
from src.p8.operator_run_config import OperatorRunRequest, prepare_operator_run
from tests.test_automation_interaction import (
    FakeBoundingBox, FakeExtent, FakeGeometry, FakeRotation, FakeVehicle,
    RecordingBackend, geometry,
)
from tests.test_lane_change_execution_lifecycle import make_rig, drive_to_completion
from tests.test_p8_operator_backend import StrictClock, register_dev


def runtime_config():
    return _runtime_config(build_parser().parse_args([
        "--participant-id", "ACTIVATION-TEST", "--run-id", "ACTIVATION-TEST-RUN",
        "--m1", "NO_SURT", "--m2", "MANUAL", "--ack-dev-live", "--spawn-index", "344",
    ]))


def controller(context, *, box=None, available=True):
    backend, events = RecordingBackend(), []
    subject = AutomationInteractionController(
        runtime=AutomationRuntimeController(
            AutomationState(AutomationAvailability.AVAILABLE if available
                            else AutomationAvailability.UNAVAILABLE, DrivingControlMode.MANUAL),
            backend,
        ),
        geometry=FakeGeometry(context), vehicle=FakeVehicle(box or FakeBoundingBox()),
        steering_target=backend, config=runtime_config().interaction_policy,
        event_sink=events.append,
    )
    return subject, backend, events


@pytest.mark.parametrize("lateral", [-.84, -.6, -.21, .21, .6, .84])
def test_within_lane_off_center_activation_and_reactivation(lateral):
    subject, backend, events = controller(geometry(lateral=lateral))
    subject.update(DriverInput())
    subject.after_control_applied()
    assert subject.state.availability is AutomationAvailability.AVAILABLE
    subject.update(DriverInput(activation_requested=True))
    assert subject.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.calls == ["noa"]
    subject.update(DriverInput(brake=.1))
    assert subject.state.control_mode is DrivingControlMode.MANUAL
    subject.update(DriverInput(activation_requested=True))
    assert subject.state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert backend.calls == ["noa", "manual", "noa"]
    assert all(event.failure_reason is None for event in events)


@pytest.mark.parametrize("width", [2.8, 3.5, 4.0])
@pytest.mark.parametrize("direction", [-1, 1])
def test_dynamic_vehicle_width_edge_inside_and_outside(width, direction):
    box = FakeBoundingBox(extent=FakeExtent(2.3, 1.0))
    margin = width / 2 - box.extent.y
    for difference, expected in ((-1e-6, None), (1e-6, ActivationFailureReason.VEHICLE_STRADDLING)):
        gate = AutomationActivationGate(
            FakeGeometry(geometry(lateral=direction * (margin + difference), lane_width=width)),
            FakeVehicle(box), runtime_config().interaction_policy,
        )
        assert gate.failure(DriverInput(activation_requested=True)) is expected


@pytest.mark.parametrize("heading,box_yaw,offset_y", [
    (.09, 0, 0), (-.09, 0, 0), (0, 5, 0), (0, -5, .12), (.04, 3, -.12),
])
def test_rotated_corners_and_box_offsets_remain_inside_same_lane(heading, box_yaw, offset_y):
    box = FakeBoundingBox(
        extent=FakeExtent(2.3, .9), location=FakeExtent(.1, offset_y),
        rotation=FakeRotation(box_yaw),
    )
    half_width = abs(sin(heading + radians(box_yaw))) * 2.3 + abs(cos(heading + radians(box_yaw))) * .9
    box_shift = .1 * sin(heading) + offset_y * cos(heading)
    for side in (-1, 1):
        for delta, expected in ((-1e-6, None), (1e-6, ActivationFailureReason.VEHICLE_STRADDLING)):
            lateral = side * (1.75 - half_width + delta) - box_shift
            subject, backend, events = controller(geometry(lateral=lateral, heading=heading), box=box)
            subject.update(DriverInput(activation_requested=True))
            if expected is None:
                assert subject.state.control_mode is DrivingControlMode.NOA_ACTIVE
            else:
                assert backend.calls == [] and events[-1].failure_reason is expected


@pytest.mark.parametrize("case,expected", [
    ("heading", ActivationFailureReason.HEADING_MISALIGNED),
    ("opposite", ActivationFailureReason.HEADING_MISALIGNED),
    ("brake", ActivationFailureReason.DRIVER_BRAKE_ACTIVE),
    ("lane_change", ActivationFailureReason.LANE_CHANGE_IN_PROGRESS),
    ("no_lane", ActivationFailureReason.GEOMETRY_UNAVAILABLE),
    ("invalid_width", ActivationFailureReason.INVALID_GEOMETRY),
    ("invalid_box", ActivationFailureReason.INVALID_GEOMETRY),
    ("external_unavailable", ActivationFailureReason.UNAVAILABLE),
])
def test_other_activation_guards_remain(case, expected):
    context, box = geometry(lateral=.3), None
    driver = DriverInput(activation_requested=True)
    if case in ("heading", "opposite"):
        context = geometry(heading=.10001 if case == "heading" else 3.141592653589793)
    elif case == "brake":
        driver = replace(driver, brake=.05)
    elif case == "lane_change":
        driver = replace(driver, lane_change_in_progress=True)
    elif case == "no_lane":
        context = None
    elif case == "invalid_width":
        context = replace(context, lane_width_m=0)
    elif case == "invalid_box":
        box = FakeBoundingBox(extent=FakeExtent(float("nan"), .9))
    subject, backend, events = controller(context, box=box, available=case != "external_unavailable")
    subject.update(driver)
    assert subject.state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == [] and events[-1].failure_reason is expected


@pytest.mark.parametrize("driver,reason", [
    (DriverInput(brake=.05), DeactivationReason.DRIVER_BRAKE),
    (DriverInput(steering_engaged=True, steering=.1), DeactivationReason.DRIVER_STEERING),
    (DriverInput(deactivation_requested=True), DeactivationReason.DRIVER_BUTTON),
])
def test_off_center_activation_keeps_immediate_driver_disengagement(driver, reason):
    subject, backend, events = controller(geometry(lateral=.6))
    subject.update(DriverInput(activation_requested=True))
    subject.update(driver)
    assert subject.state.control_mode is DrivingControlMode.MANUAL
    assert backend.calls == ["noa", "manual"] and events[-1].deactivation_reason is reason
    assert backend.steering_overrides[-1] is None


def test_activation_with_held_steering_still_disengages_in_same_update():
    subject, _, events = controller(geometry(lateral=-.6))
    subject.update(DriverInput(activation_requested=True, steering_engaged=True, steering=.1))
    assert subject.state.control_mode is DrivingControlMode.MANUAL
    assert events[-1].deactivation_reason is DeactivationReason.DRIVER_STEERING


@pytest.mark.parametrize("stage,module,condition", [
    (Stage.TRAINING, ExperimentModule.MODULE_1, Module1Condition.NO_SURT),
    (Stage.MODULE_1_RUNNING, ExperimentModule.MODULE_1, Module1Condition.NO_SURT),
    (Stage.MODULE_1_RUNNING, ExperimentModule.MODULE_1, Module1Condition.SURT),
    (Stage.MODULE_2_RUNNING, ExperimentModule.MODULE_2, Module2Condition.MANUAL),
    (Stage.MODULE_2_RUNNING, ExperimentModule.MODULE_2, Module2Condition.NOA_L2),
])
def test_production_policy_reaches_training_m1_m2_and_initial_noa(tmp_path, stage, module, condition):
    operator, _ = register_dev(
        tmp_path, StrictClock(),
        m1=condition if module is ExperimentModule.MODULE_1 else Module1Condition.NO_SURT,
        m2=condition if module is ExperimentModule.MODULE_2 else Module2Condition.MANUAL,
    )
    runtime = runtime_config()
    request = OperatorRunRequest(
        module, stage, 780 if module is ExperimentModule.MODULE_2 else 600,
        ExperimentPhase.TRAINING if stage is Stage.TRAINING else None,
        nominal_request=stage is not Stage.TRAINING,
    )
    prepared = prepare_operator_run(operator._operator_components().services, runtime, request)
    policy = prepared.config.automation_interaction.policy
    assert prepared.config.automation_interaction.condition == condition
    assert not policy.require_centered_activation
    assert policy.heading_tolerance_rad == .1
    assert policy.driver_brake_threshold == .05
    assert policy.steering_deactivation_enabled and policy.button_deactivation_enabled
    subject, backend, _ = controller(geometry(lateral=-.6))
    subject.initialize(module, condition, stage=stage.value)
    if condition is Module2Condition.NOA_L2:
        assert backend.calls == ["noa"]
    else:
        assert subject.state.availability is AutomationAvailability.AVAILABLE
        assert backend.calls == []
        subject.update(DriverInput(activation_requested=True))
    assert subject.state.control_mode is DrivingControlMode.NOA_ACTIVE


def test_changed_policy_is_kept_in_exported_components_metadata(tmp_path):
    operator, _ = register_dev(tmp_path, StrictClock())
    components = operator._operator_components()
    components.runtime = replace(components.runtime, interaction_policy=runtime_config().interaction_policy)
    assert components.snapshot()["automation_interaction_policy"] == asdict(runtime_config().interaction_policy)


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
def test_new_activation_policy_does_not_complete_lane_change_early_or_break_signal(direction):
    rig = make_rig(direction, submit_general=False)
    policy = runtime_config().interaction_policy
    # Both lane-change request and completion receive the production policy.
    rig.observer._completion_config = policy
    rig.observer._requests._config = policy
    assert lane_alignment_failure(geometry(lateral=.6), FakeBoundingBox(), policy) is ActivationFailureReason.NOT_CENTERED
    rig.observer.update(DriverInput(lane_change_direction=direction))
    drive_to_completion(rig, direction)  # Asserts still in flight at 0.7m offset.
    assert rig.backend.lane_change_in_flight is None
    assert not rig.observer.lane_change_in_progress
    assert not rig.vehicle.current_light_state & (
        carla.VehicleLightState.LeftBlinker | carla.VehicleLightState.RightBlinker)


def test_legacy_explicit_centered_activation_remains_available():
    policy = replace(runtime_config().interaction_policy, require_centered_activation=True)
    gate = AutomationActivationGate(FakeGeometry(geometry(lateral=.6)), FakeVehicle(), policy)
    assert gate.failure(DriverInput()) is ActivationFailureReason.NOT_CENTERED
