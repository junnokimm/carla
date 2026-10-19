from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from math import isfinite

from src.error_notes import append_error_note
from src.experiment.automation_interaction import DriverInput
from src.experiment.context import ExperimentModule, ExperimentPhase
from src.experiment.timestamp import TimestampEnvelope
from src.p7.display import HazardDisplayBackend, HazardFrameVisibility
from src.p7.events import HazardEventLogger
from src.p7.hazard import HazardScenario
from src.p7.metrics import canonical_hazard_record
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import HazardObservation, HazardOutcome, HazardState, Json, P7Error
from src.p8.model import Stage
from src.p8.operator_hazard_contracts import (
    HazardCleanupEvidence,
    HazardExecutionPlan,
    HazardExecutionResources,
)
from src.scenario.camera_feed import DisplayedCameraFrame
from src.scenario.research_noa_lifecycle import retain_first_error


class OperatorHazardLoop:
    def __init__(
        self,
        plan: HazardExecutionPlan,
        resources: HazardExecutionResources,
        occurrences: OccurrenceRegistry,
        record_sink: Callable[[dict[str, Json]], None],
    ) -> None:
        self._plan = plan
        self._resources = resources
        self._occurrences = occurrences
        self._record_sink = record_sink
        self._backend = None
        self._scenario: HazardScenario | None = None
        self._last: HazardObservation | None = None
        self._tick_count = 0
        self._rescue_status = "NOT_REQUESTED"
        self._finalized = False
        self.record: dict[str, Json] | None = None
        self._started = False
        self._end_reason: str | None = None
        self._display_candidate: dict[str, Json] | None = None
        self._visibility_status = "DETECTOR_NOT_BOUND"

    def start(self) -> None:
        if self._started or self._finalized:
            raise P7Error("HAZARD_LOOP_ALREADY_STARTED_OR_FINALIZED")
        self._started = True
        persistence = self._resources.session.persistence
        if persistence is None:
            raise P7Error("OPERATOR_HAZARD_PERSISTENCE_REQUIRED")
        persistence.begin_segment(
            self._resources.services.segment(), Stage.FINAL_HAZARD.value
        )
        try:
            self._backend = self._plan.backend_factory.acquire(self._resources)
        except BaseException as error:
            try:
                cleanup = self._plan.backend_factory.cleanup_partial()
            except (OSError, RuntimeError, TypeError, ValueError) as cleanup_error:
                cleanup = HazardCleanupEvidence(
                    (f"{type(cleanup_error).__name__}:{cleanup_error}",), ()
                )
                append_error_note(
                    error,
                    "Partial hazard backend cleanup failure: "
                    f"{type(cleanup_error).__name__}:{cleanup_error}",
                )
            self._finish(error, cleanup)
            raise
        services = self._resources.services
        self._scenario = HazardScenario(
            self._plan.approval.config,
            module=ExperimentModule.MODULE_2,
            phase=ExperimentPhase.FINAL_HAZARD_ASSESSMENT,
            participant_id=str(services.context.participant_id),
            run_id=str(services.context.study_run_id),
            actors=self._backend.actors,
            occurrences=self._occurrences,
            event_sink=HazardEventLogger(services.logger, services.segment()).capture,
            origin_ns=services.clock.origin.monotonic_ns,
            session_clock=services.clock,
            policy=self._plan.approval.policy,
        )

    def after_control_applied(self, driver_input: DriverInput) -> bool:
        if self._finalized:
            return False
        backend = self._backend
        scenario = self._scenario
        persistence = self._resources.session.persistence
        if backend is None or scenario is None or persistence is None:
            raise P7Error("OPERATOR_HAZARD_LOOP_NOT_STARTED")
        sample = persistence.source.last_snapshot_sample
        if sample is None:
            raise P7Error("HAZARD_FRAME_PERSISTENCE_SAMPLE_REQUIRED")
        geometry = backend.observe_geometry(sample)
        if geometry is None:
            if self._last is not None:
                persistence.flush_events()
                scenario.abort(
                    self._last,
                    "OBSERVATION_STREAM_ENDED",
                    HazardOutcome.UNOBSERVABLE,
                    event_timestamp=self._resources.services.stamp(),
                )
            self._end_reason = "OBSERVATION_STREAM_ENDED"
            return False
        if geometry.timestamp != sample.timestamp:
            raise P7Error("HAZARD_GEOMETRY_SAMPLE_MISMATCH")
        observation = replace(
            geometry,
            driver_input=driver_input,
            input_source="INJECTED_TEST_DRIVER_INPUT",
        )
        self._last = observation
        try:
            if scenario.state is HazardState.IDLE:
                if not scenario.arm(observation):
                    raise P7Error("APPROVED_HAZARD_DID_NOT_ARM")
            else:
                scenario.update(observation)
        except BaseException as error:
            if (
                scenario.termination_requested
                and self._rescue_status == "NOT_REQUESTED"
            ):
                retain_first_error(
                    lambda: self._request_safe_shutdown(observation), error
                )
            raise
        self._tick_count += 1
        if scenario.termination_requested and self._rescue_status == "NOT_REQUESTED":
            self._request_safe_shutdown(observation)
        return scenario.state not in (HazardState.COMPLETED, HazardState.ABORTED)

    def after_display_completed(self, frame: DisplayedCameraFrame | None) -> bool:
        scenario = self._scenario
        if self._finalized or scenario is None:
            return False
        active = scenario.state not in (HazardState.COMPLETED, HazardState.ABORTED)
        if not active:
            return False
        if self._display_candidate is not None:
            return active
        if not isinstance(self._backend, HazardDisplayBackend):
            return active
        if frame is None or frame.frame is None or frame.simulation_seconds is None:
            self._visibility_status = "DISPLAY_FRAME_UNOBSERVABLE"
            return active
        if (
            type(frame.frame) is not int
            or frame.frame < 0
            or not isfinite(frame.simulation_seconds)
            or frame.simulation_seconds < 0
            or type(frame.flip_completed_monotonic_ns) is not int
            or self._last is None
            or frame.flip_completed_monotonic_ns
            < self._last.timestamp.host.monotonic_ns
        ):
            raise P7Error("INVALID_HAZARD_DISPLAY_TIME")
        verdict = self._backend.visibility_for_frame(frame)
        if verdict is None:
            self._visibility_status = "FRAME_VISIBILITY_UNOBSERVABLE"
            return active
        if (
            not isinstance(verdict, HazardFrameVisibility)
            or verdict.sensor_actor_id != frame.sensor_actor_id
            or verdict.frame != frame.frame
            or type(verdict.visible) is not bool
            or not verdict.evidence_source.strip()
        ):
            raise P7Error("HAZARD_DISPLAY_EVIDENCE_MISMATCH")
        self._visibility_status = "FRAME_OBSERVED_NOT_VISIBLE"
        if verdict.visible:
            self._visibility_status = "VISIBLE_FRAME_CANDIDATE_NOT_PHOTON_VALIDATED"
            self._display_candidate = {
                "sensor_actor_id": frame.sensor_actor_id,
                "frame": frame.frame,
                "simulation_seconds": frame.simulation_seconds,
                "receipt_perf_counter_seconds": frame.receipt_perf_counter_seconds,
                "flip_completed_monotonic_ns": frame.flip_completed_monotonic_ns,
                "evidence_source": verdict.evidence_source,
                "participant_perception": "NOT_MEASURED",
                "hazard_visible_t": None,
            }
            persistence = self._resources.session.persistence
            if persistence is not None:
                persistence.flush_events()
            self._resources.services.emit(
                "hazard_display_observation", self._display_candidate
            )
            if scenario.metrics.trigger_t is None and self._last is not None:
                scenario.abort(
                    self._last,
                    "VISIBLE_BEFORE_TRIGGER",
                    HazardOutcome.UNOBSERVABLE,
                    event_timestamp=self._resources.services.stamp(),
                )
                return False
        return active

    def finalize(self, reason: str, error: BaseException | None = None) -> None:
        if self._finalized:
            return
        self._end_reason = self._end_reason or reason
        scenario = self._scenario
        if (
            scenario is not None
            and self._last is not None
            and scenario.state not in (HazardState.COMPLETED, HazardState.ABORTED)
        ):
            if self._resources.session.persistence is not None:
                error = retain_first_error(
                    self._resources.session.persistence.flush_events, error
                )
            try:
                scenario.abort(
                    self._last,
                    reason
                    if error is None
                    else f"EXECUTION_ERROR:{type(error).__name__}:{error}",
                    scenario.outcome or HazardOutcome.UNOBSERVABLE,
                    event_timestamp=self._resources.services.stamp(),
                )
            except (OSError, RuntimeError, TypeError, ValueError) as abort_error:
                if error is None:
                    error = abort_error
                else:
                    append_error_note(
                        error,
                        "Hazard abort logging failure: "
                        f"{type(abort_error).__name__}:{abort_error}",
                    )
        cleanup = HazardCleanupEvidence((), ())
        try:
            if self._backend is not None:
                cleanup = self._backend.cleanup()
        except (OSError, RuntimeError, TypeError, ValueError) as cleanup_error:
            cleanup = HazardCleanupEvidence(
                (f"{type(cleanup_error).__name__}:{cleanup_error}",),
                (),
            )
            if error is None:
                error = cleanup_error
            else:
                append_error_note(
                    error,
                    "Hazard actor cleanup failure: "
                    f"{type(cleanup_error).__name__}:{cleanup_error}",
                )
        error = self._finish(error, cleanup)
        if error is not None:
            raise error

    def _finish(
        self, error: BaseException | None, cleanup: HazardCleanupEvidence
    ) -> BaseException | None:
        if self._finalized:
            return error
        if cleanup.failures or cleanup.remaining_actor_ids:
            cleanup_error = RuntimeError("HAZARD_ACTOR_CLEANUP_INCOMPLETE")
            if error is None:
                error = cleanup_error
            else:
                append_error_note(error, str(cleanup_error))
        self._finalized = True
        services = self._resources.services
        resources = self._resources
        scenario = self._scenario
        persistence = resources.session.persistence
        final_timestamps: list[TimestampEnvelope] = []
        error = retain_first_error(
            lambda: final_timestamps.append(services.stamp()), error
        )
        final_timestamp = None if not final_timestamps else final_timestamps[0]
        elapsed_seconds: list[float] = []
        if final_timestamp is not None:
            error = retain_first_error(
                lambda: elapsed_seconds.append(
                    services.clock.project_elapsed_seconds(final_timestamp.host)
                ),
                error,
            )
        record: dict[str, Json] = {
            **canonical_hazard_record(None if scenario is None else scenario.outcome),
            "participant_id": services.context.participant_id,
            "run_id": services.context.study_run_id,
            "session_elapsed_s": None if not elapsed_seconds else elapsed_seconds[0],
            "m2_condition": services.context.module_2_condition.value,
            "auto_state": resources.identities.control_mode,
            "auto_state_at_hazard_end": resources.session.bundle.automation_runtime.state.control_mode.value,
            "last_observation_host_monotonic_ns": None
            if self._last is None
            else self._last.timestamp.host.monotonic_ns,
            "execution_status": (
                "FAILED_OFFLINE"
                if error is not None
                else "EXECUTED_OFFLINE"
                if scenario is not None and scenario.state is HazardState.COMPLETED
                else "ABORTED_OFFLINE"
            ),
            "execution_error": None
            if error is None
            else f"{type(error).__name__}:{error}",
            "observation_status": (
                "NOT_STARTED" if scenario is None else scenario.state.value
            ),
            "hazard_state": (
                "NOT_STARTED" if scenario is None else scenario.state.value
            ),
            "tick_count": self._tick_count,
            "policy_source": self._plan.approval.policy_source,
            "approval_reference": self._plan.approval.approval_reference,
            "backend_readiness_source": self._plan.approval.backend_readiness_source,
            "execution_scope": "OFFLINE_TEST",
            "visibility_status": self._visibility_status,
            "first_displayed_frame": None
            if self._display_candidate is None
            else self._display_candidate["frame"],
            "display_candidate": self._display_candidate,
            "candidate_observation": None
            if scenario is None
            else scenario.metrics.snapshot(),
            "terminal_reason": self._end_reason
            if scenario is None
            else scenario.terminal_reason or self._end_reason,
            "occurrence_status": "RESERVED_NO_AUTOMATIC_RETRY"
            if scenario is not None and scenario.reservation_claimed
            else "NOT_CLAIMED",
            "actors_prepared": scenario is not None and scenario.actors_prepared,
            "trigger_policy": "DEV_STATION_ONLY"
            if self._plan.approval.policy is None
            else "INJECTED_POLICY",
            "input_time_basis": "PERSISTENCE_AFTER_CONTROL_NOT_INPUT_POLL_TIME",
            "rescue_status": self._rescue_status,
            "physical_stop_success": None,
            "cleanup_failures": list(cleanup.failures),
            "remaining_owned_actor_ids": list(cleanup.remaining_actor_ids),
            "runtime_continuity_scope": "SAME_LIVE_SESSION_PRE_SHUTDOWN",
            "shared_resource_identities": {
                "session": resources.identities.session,
                "world": resources.identities.world,
                "hero": resources.identities.hero,
                "viewer_sensor_owner": resources.identities.viewer_sensor_owner,
                "control": resources.identities.control,
                "persistence": resources.identities.persistence,
                "logger": resources.identities.logger,
                "session_clock": resources.identities.session_clock,
            },
        }
        self.record = record
        error = retain_first_error(lambda: self._record_sink(record), error)
        if persistence is not None:
            error = retain_first_error(persistence.flush_events, error)
            error = retain_first_error(
                lambda: persistence.end_stage(
                    f"FAILURE:{type(error).__name__}"
                    if error is not None
                    else "NORMAL_COMPLETION"
                    if scenario is not None and scenario.state is HazardState.COMPLETED
                    else f"HAZARD_ABORTED:{record['terminal_reason']}"
                ),
                error,
            )
        error = retain_first_error(
            lambda: services.emit("hazard_observation", record),
            error,
        )
        return error

    def _request_safe_shutdown(self, observation: HazardObservation) -> None:
        services = self._resources.services
        persistence = self._resources.session.persistence
        error: BaseException | None = None
        self._rescue_status = "REQUESTED"
        if persistence is not None:
            error = retain_first_error(persistence.flush_events, error)
        error = retain_first_error(
            lambda: services.emit(
                "hazard_safe_shutdown",
                {"status": "REQUESTED", "physical_stop_success": None},
            ),
            error,
        )

        def request() -> None:
            self._rescue_status = "FAILED"
            self._plan.safety_owner.request_safe_shutdown(observation.timestamp)
            self._rescue_status = "RETURNED_NOT_PHYSICAL_SUCCESS"

        error = retain_first_error(request, error)
        error = retain_first_error(
            lambda: services.emit(
                "hazard_safe_shutdown",
                {"status": self._rescue_status, "physical_stop_success": None},
            ),
            error,
        )
        if error is not None:
            raise error
