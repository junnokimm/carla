from __future__ import annotations

from collections.abc import Callable
from typing import assert_never

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_activation_gate import AutomationActivationGate
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    ActivationGeometry,
    ActivationVehicle,
    AutomationInteractionConditionError,
    AutomationInteractionConfig,
    AutomationInteractionEvent,
    AutomationInteractionEventType,
    DeactivationReason,
    DriverInput,
    SteeringOverrideTarget,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.context import (
    ExperimentCondition,
    ExperimentModule,
    Module1Condition,
    Module2Condition,
)


class AutomationInteractionController:
    def __init__(
        self,
        *,
        runtime: AutomationRuntimeController,
        geometry: ActivationGeometry,
        vehicle: ActivationVehicle,
        steering_target: SteeringOverrideTarget,
        config: AutomationInteractionConfig,
        event_sink: Callable[[AutomationInteractionEvent], None],
    ) -> None:
        self._runtime = runtime
        self.geometry = geometry
        self._vehicle = vehicle
        self._gate = AutomationActivationGate(geometry, vehicle, config)
        self._steering_target = steering_target
        self._config = config
        self._event_sink = event_sink
        self._initialization_failure: ActivationFailureReason | None = None
        self._pending_driver_input: DriverInput | None = None
        self._external_available = (
            self.state.availability is AutomationAvailability.AVAILABLE
        )

    @property
    def state(self) -> AutomationState:
        return self._runtime.state

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        match module:
            case ExperimentModule.MODULE_1:
                match condition:
                    case Module1Condition.NO_SURT | Module1Condition.SURT:
                        activate = False
                    case Module2Condition.MANUAL | Module2Condition.NOA_L2:
                        raise AutomationInteractionConditionError(module, condition)
                    case unreachable:
                        assert_never(unreachable)
            case ExperimentModule.MODULE_2:
                match condition:
                    case Module2Condition.MANUAL:
                        activate = False
                    case Module2Condition.NOA_L2:
                        activate = True
                    case Module1Condition.NO_SURT | Module1Condition.SURT:
                        raise AutomationInteractionConditionError(module, condition)
                    case unreachable:
                        assert_never(unreachable)
            case unreachable:
                assert_never(unreachable)
        driver_input = DriverInput(
            lane_change_in_progress=lane_change_in_progress,
            stage=stage,
        )
        if activate:
            failure = self._request_activation_before_gate(driver_input)
        else:
            failure = self._refresh_availability(driver_input)
        self._initialization_failure = failure if activate else None
        self._emit(AutomationInteractionEventType.INITIAL_STATE, stage=stage)
        return self.state

    @property
    def initialization_failure(self) -> ActivationFailureReason | None:
        return self._initialization_failure

    def update(self, driver_input: DriverInput) -> AutomationState:
        self._pending_driver_input = driver_input
        if self.state.control_mode is DrivingControlMode.MANUAL:
            if driver_input.activation_requested:
                self._request_activation_before_gate(driver_input)
            override = (
                driver_input.steering
                if self.state.control_mode is DrivingControlMode.NOA_ACTIVE
                and driver_input.steering_engaged
                else None
            )
            self._steering_target.set_driver_steering(override)
            return self.state
        if (
            self.state.control_mode is DrivingControlMode.NOA_ACTIVE
            and driver_input.brake >= self._config.driver_brake_threshold
        ):
            self._disengage(DeactivationReason.DRIVER_BRAKE, driver_input.stage)
            return self.state
        if (
            driver_input.deactivation_requested
            and self.state.control_mode is DrivingControlMode.NOA_ACTIVE
            and self._config.button_deactivation_enabled
        ):
            self._emit(
                AutomationInteractionEventType.REQUEST,
                requested_mode=DrivingControlMode.MANUAL,
                stage=driver_input.stage,
            )
            self._disengage(DeactivationReason.DRIVER_BUTTON, driver_input.stage)
            return self.state
        override = (
            driver_input.steering
            if self.state.control_mode is DrivingControlMode.NOA_ACTIVE
            and driver_input.steering_engaged
            else None
        )
        self._steering_target.set_driver_steering(override)
        return self.state

    def after_control_applied(self) -> None:
        driver_input = self._pending_driver_input
        self._pending_driver_input = None
        if (
            driver_input is not None
            and self.state.control_mode is DrivingControlMode.MANUAL
        ):
            self._refresh_availability(driver_input)

    def set_external_availability(
        self,
        availability: AutomationAvailability,
        *,
        reason: DeactivationReason,
        stage: str | None = None,
    ) -> AutomationState:
        match availability:
            case AutomationAvailability.AVAILABLE:
                self._external_available = True
            case AutomationAvailability.UNAVAILABLE:
                self._external_available = False
                previous_mode = self.state.control_mode
                previous_availability = self.state.availability
                self._runtime.set_availability(AutomationAvailability.UNAVAILABLE)
                if previous_availability is AutomationAvailability.AVAILABLE:
                    self._emit(
                        AutomationInteractionEventType.AVAILABILITY,
                        failure_reason=ActivationFailureReason.UNAVAILABLE,
                        stage=stage,
                    )
                if previous_mode is DrivingControlMode.NOA_ACTIVE:
                    self._emit_disengagement(reason, stage)
            case unreachable:
                assert_never(unreachable)
        return self.state

    def _request_activation_before_gate(
        self, driver_input: DriverInput
    ) -> ActivationFailureReason | None:
        self._emit(
            AutomationInteractionEventType.REQUEST,
            requested_mode=DrivingControlMode.NOA_ACTIVE,
            stage=driver_input.stage,
        )
        failure = self._refresh_availability(driver_input)
        if failure is not None:
            self._emit(
                AutomationInteractionEventType.FAILURE,
                requested_mode=DrivingControlMode.NOA_ACTIVE,
                failure_reason=failure,
                stage=driver_input.stage,
            )
            return failure
        self._runtime.request_control_mode(DrivingControlMode.NOA_ACTIVE)
        self._emit(
            AutomationInteractionEventType.TRANSITION,
            requested_mode=DrivingControlMode.NOA_ACTIVE,
            stage=driver_input.stage,
        )
        return None

    def _refresh_availability(
        self, driver_input: DriverInput
    ) -> ActivationFailureReason | None:
        failure = (
            self._gate.failure(driver_input)
            if self._external_available
            else ActivationFailureReason.UNAVAILABLE
        )
        availability = (
            AutomationAvailability.AVAILABLE
            if failure is None
            else AutomationAvailability.UNAVAILABLE
        )
        if availability is not self.state.availability:
            self._runtime.set_availability(availability)
            self._emit(
                AutomationInteractionEventType.AVAILABILITY,
                failure_reason=failure,
                stage=driver_input.stage,
            )
        return failure

    def _disengage(self, reason: DeactivationReason, stage: str | None) -> None:
        self._runtime.request_control_mode(DrivingControlMode.MANUAL)
        self._steering_target.set_driver_steering(None)
        self._emit_disengagement(reason, stage)

    def _emit_disengagement(
        self, reason: DeactivationReason, stage: str | None
    ) -> None:
        self._emit(
            AutomationInteractionEventType.TRANSITION,
            requested_mode=DrivingControlMode.MANUAL,
            deactivation_reason=reason,
            stage=stage,
        )
        self._emit(
            AutomationInteractionEventType.DISENGAGEMENT,
            requested_mode=DrivingControlMode.MANUAL,
            deactivation_reason=reason,
            stage=stage,
        )

    def _emit(
        self,
        event_type: AutomationInteractionEventType,
        *,
        requested_mode: DrivingControlMode | None = None,
        failure_reason: ActivationFailureReason | None = None,
        deactivation_reason: DeactivationReason | None = None,
        stage: str | None = None,
    ) -> None:
        self._event_sink(
            AutomationInteractionEvent(
                event_type=event_type,
                state=self.state,
                requested_mode=requested_mode,
                failure_reason=failure_reason,
                deactivation_reason=deactivation_reason,
                stage=stage,
            )
        )
