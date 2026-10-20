from __future__ import annotations

import carla

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.lane_change import LaneChangeDirection
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.lane_change_contracts import (
    LaneChangeCorrelation,
    LaneChangeEventPersistence,
    LaneChangeExecutor,
    LaneChangeInteraction,
    LaneChangeVehicle,
    TrafficLightRecorder,
)
from src.scenario.lane_change_events import (
    LaneChangeEventSink,
    lifecycle_payload,
    record_geometry_observation,
)
from src.scenario.lane_change_execution import (
    LaneChangeSubmissionStatus,
    StaleLaneChangeGenerationError,
)
from src.scenario.lane_change_geometry import (
    LaneChangeObservationResult,
    LaneChangeObservationStatus,
)
from src.scenario.lane_change_lifecycle import LaneChangeTracker
from src.scenario.lane_change_request_controller import LaneChangeRequestController
from src.scenario.lane_change_signal import clear_completed_signal
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter


class ResearchLaneChangeInteractionObserver:
    def __init__(
        self,
        interaction: LaneChangeInteraction,
        adjacent_lanes: CarlaAdjacentLaneAdapter,
        vehicle: LaneChangeVehicle,
        persistence: LaneChangeEventPersistence,
        correlation: LaneChangeCorrelation,
        traffic_light: TrafficLightRecorder | None = None,
        executor: LaneChangeExecutor | None = None,
        completion_config: AutomationInteractionConfig | None = None,
    ) -> None:
        self._interaction = interaction
        self._vehicle = vehicle
        self._persistence = persistence
        self._events = LaneChangeEventSink(persistence)
        self._correlation = correlation
        self._traffic_light = traffic_light
        self._executor = executor
        self._completion_config = completion_config
        self._requests = LaneChangeRequestController(
            adjacent_lanes,
            vehicle,
            persistence,
            self._events,
            correlation,
            completion_config,
        )
        self._tracker: LaneChangeTracker | None = None
        self._signal_wait_started_ns: int | None = None
        self._signal_request_count = 0
        self._failed_p5_submission: TimestampEnvelope | None = None

    @property
    def signal_request_count(self) -> int:
        return self._signal_request_count

    def take_failed_p5_submission(self) -> TimestampEnvelope | None:
        timestamp = self._failed_p5_submission
        self._failed_p5_submission = None
        return timestamp

    @property
    def state(self) -> AutomationState:
        return self._interaction.state

    @property
    def initialization_failure(self) -> ActivationFailureReason | None:
        return self._interaction.initialization_failure

    @property
    def lane_change_in_progress(self) -> bool:
        return self._tracker is not None

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        return self._interaction.initialize(
            module,
            condition,
            lane_change_in_progress=lane_change_in_progress,
            stage=stage,
        )

    def update(self, driver_input: DriverInput) -> AutomationState:
        state = self._interaction.update(driver_input)
        if (
            self._tracker is not None
            and state.control_mode is DrivingControlMode.MANUAL
        ):
            self._finish("INTERRUPTED", "MANUAL_PRIORITY")
        direction = driver_input.lane_change_direction
        if direction is None:
            return state
        p5 = (
            direction is LaneChangeDirection.RIGHT
            and driver_input.exit_confirm_requested
            and self._correlation.lc_event_id is not None
        )
        self._request(direction, state, driver_input, p5=p5, submit=not p5)
        return state

    def submit_confirmed_p5(self) -> bool:
        if self._tracker is not None:
            self._request(
                LaneChangeDirection.RIGHT,
                self.state,
                DriverInput(exit_confirm_requested=True),
                p5=True,
                submit=False,
            )
            return False
        self._request(
            LaneChangeDirection.RIGHT,
            self.state,
            DriverInput(exit_confirm_requested=True),
            p5=True,
            submit=False,
        )
        # Confirmation accepts an intent. Native submission is recorded only
        # when the asynchronous light readback permits _submit to send it.
        submitted = self._submit()
        return submitted or (self._tracker is not None and not self._tracker.submitted)

    def request_general(self, direction: LaneChangeDirection) -> None:
        """Dispatch a signal not consumed by an exit confirmation, once per edge."""
        self._request(direction, self.state, DriverInput(lane_change_direction=direction),
                      p5=False, submit=True)

    def after_control_applied(self) -> None:
        self._interaction.after_control_applied()
        sample = self._persistence.source.last_snapshot_sample
        if self._traffic_light is not None and sample is not None:
            self._traffic_light.record(sample, self.state)
        tracker = self._tracker
        config = self._completion_config
        if tracker is None or config is None:
            return
        if not tracker.submitted:
            # Vehicle light commands and the server's light-state readback can
            # straddle one iteration. Wait without blocking input or re-toggling.
            if self.state.control_mode is DrivingControlMode.NOA_ACTIVE:
                try:
                    self._submit()
                except (RuntimeError, ValueError, OSError) as error:
                    if tracker.context != "AUTO_EXIT":
                        raise
                    now = self._persistence.source.get_host_timestamp()
                    self._record_lifecycle(
                        "UNVERIFIED", f"AUTO_DEFERRED_SUBMISSION:{type(error).__name__}:{error}", now
                    )
                    self._failed_p5_submission = now
                    if self._executor is None or self._executor.lane_change_in_flight is None:
                        self._tracker = None
            return
        if sample is None:
            if tracker.unverified_reason == "MISSING_POST_CONTROL_SNAPSHOT":
                return
            result = tracker.mark_unverified("MISSING_POST_CONTROL_SNAPSHOT")
            self._record_lifecycle(
                "UNVERIFIED",
                "MISSING_POST_CONTROL_SNAPSHOT",
                result=result,
            )
            return
        previous_unverified = tracker.unverified_reason
        result = tracker.observe(sample, self._vehicle.bounding_box, config)
        if result.status is LaneChangeObservationStatus.IGNORED:
            return
        record_geometry_observation(self._events, tracker, result, sample)
        if previous_unverified is not None and not tracker.unverified:
            self._record_lifecycle("OBSERVATION_RESUMED", previous_unverified, sample.timestamp, result)
        if result.status is LaneChangeObservationStatus.IN_PROGRESS:
            self._record_lifecycle("IN_PROGRESS", None, sample.timestamp, result)
        elif result.status is LaneChangeObservationStatus.UNVERIFIED:
            if result.reason != previous_unverified:
                self._record_lifecycle(
                    "UNVERIFIED", result.reason, sample.timestamp, result
                )
        elif result.status is LaneChangeObservationStatus.COMPLETED:
            self._complete(sample.timestamp, result)

    def finish_lane_change_observation(
        self,
        reason: str,
        timestamp: TimestampEnvelope,
    ) -> None:
        self._finish("UNVERIFIED", reason, timestamp=timestamp)

    def _request(
        self,
        direction: LaneChangeDirection,
        state: AutomationState,
        driver_input: DriverInput,
        *,
        p5: bool,
        submit: bool,
        automatic: bool = False,
    ) -> None:
        self._signal_request_count += 1
        executor_ready = (
            self._executor is not None and self._completion_config is not None
        )
        tracker = self._requests.create(
            direction,
            state,
            driver_input,
            p5=p5,
            active_tracker=self._tracker,
            generation=(
                self._executor.activation_generation
                if executor_ready and self._executor is not None
                else None
            ),
            automatic=automatic,
        )
        if tracker is None:
            return
        self._tracker = tracker
        self._signal_wait_started_ns = None
        self._record_lifecycle("REQUESTED", None)
        if submit:
            self._submit()

    def _submit(self) -> bool:
        tracker = self._tracker
        executor = self._executor
        if tracker is None or executor is None:
            return False
        selected = (
            carla.VehicleLightState.LeftBlinker
            if tracker.direction is LaneChangeDirection.LEFT
            else carla.VehicleLightState.RightBlinker
        )
        try:
            signal_on = bool(self._vehicle.get_light_state() & selected)
        except RuntimeError:
            signal_on = False
        if not signal_on:
            now = self._persistence.source.get_host_timestamp()
            if self._signal_wait_started_ns is None:
                self._signal_wait_started_ns = now.host.monotonic_ns
                self._record_lifecycle("WAITING_FOR_SIGNAL", "SIGNAL_READBACK_PENDING", now)
            elif now.host.monotonic_ns - self._signal_wait_started_ns >= 1_000_000_000:
                self._record_lifecycle("BLOCKED", "REQUESTED_SIGNAL_NOT_OBSERVED_ON", now)
                if tracker.context in ("P5", "AUTO_EXIT"):
                    self._failed_p5_submission = now
                self._tracker = None
            return False
        try:
            result = executor.submit_lane_change(
                tracker.request_id,
                tracker.generation,
                right_lane_change=tracker.direction is LaneChangeDirection.RIGHT,
            )
        except StaleLaneChangeGenerationError:
            self._record_lifecycle(
                "REJECTED",
                "STALE_ACTIVATION_GENERATION",
            )
            self._tracker = None
            return False
        timestamp = self._persistence.source.get_host_timestamp()
        if result.status in (
            LaneChangeSubmissionStatus.SUBMITTED,
            LaneChangeSubmissionStatus.UNVERIFIED,
        ):
            submission_failure = tracker.mark_submitted(
                timestamp,
                self._persistence.source.last_snapshot_sample,
            )
        else:
            submission_failure = None
        lifecycle = (
            "SUBMITTED_TO_TM"
            if result.status is LaneChangeSubmissionStatus.SUBMITTED
            else result.status.value
        )
        self._record_lifecycle(lifecycle, result.reason, timestamp)
        if submission_failure is not None:
            self._record_lifecycle(
                "UNVERIFIED",
                submission_failure.reason,
                timestamp,
                submission_failure,
            )
        if result.status in (
            LaneChangeSubmissionStatus.BLOCKED,
            LaneChangeSubmissionStatus.REJECTED,
        ):
            self._tracker = None
        return result.status is LaneChangeSubmissionStatus.SUBMITTED

    def _complete(
        self,
        timestamp: TimestampEnvelope,
        result: LaneChangeObservationResult,
    ) -> None:
        tracker = self._tracker
        executor = self._executor
        if tracker is None or executor is None:
            return
        try:
            executor.complete_lane_change(tracker.generation)
        except StaleLaneChangeGenerationError:
            tracker.mark_unverified("STALE_ACTIVATION_GENERATION_AT_COMPLETION")
            self._record_lifecycle(
                "UNVERIFIED",
                "STALE_ACTIVATION_GENERATION_AT_COMPLETION",
                timestamp,
                result,
            )
            self._tracker = None
            return
        self._record_lifecycle("COMPLETED", None, timestamp, result)
        self._tracker = None
        clear_completed_signal(
            self._vehicle,
            self._persistence,
            self._events,
            self.state,
            tracker,
            timestamp,
        )

    def _finish(
        self,
        lifecycle: str,
        reason: str,
        *,
        invalidate: bool = True,
        timestamp: TimestampEnvelope | None = None,
    ) -> None:
        if self._tracker is None:
            return
        if (
            self._executor is not None
            and self._executor.lane_change_in_flight is not None
        ):
            self._tracker.mark_unverified("TM_DEREGISTRATION_NOT_ACKNOWLEDGED")
            self._record_lifecycle(
                "UNVERIFIED", self._tracker.unverified_reason, timestamp
            )
            return
        if invalidate and self._executor is not None:
            self._executor.interrupt_lane_change()
        self._record_lifecycle(lifecycle, reason, timestamp)
        self._tracker = None

    def _record_lifecycle(
        self,
        lifecycle: str,
        reason: str | None,
        timestamp: TimestampEnvelope | None = None,
        result: LaneChangeObservationResult | None = None,
    ) -> None:
        tracker = self._tracker
        if tracker is None:
            return
        effective_timestamp = timestamp or self._persistence.source.get_host_timestamp()
        self._events.record(
            "lane_change_lifecycle",
            lifecycle_payload(
                tracker,
                lifecycle,
                reason,
                effective_timestamp,
                result,
            ),
            effective_timestamp,
        )
