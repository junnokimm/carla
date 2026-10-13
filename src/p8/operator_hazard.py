from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import ExperimentModule, ExperimentPhase
from src.experiment.timestamp import TimestampEnvelope
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

    def start(self) -> None:
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
                error.add_note(
                    "Partial hazard backend cleanup failure: "
                    f"{type(cleanup_error).__name__}:{cleanup_error}"
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
        )

    def after_control_applied(self, driver_input: DriverInput) -> bool:
        backend = self._backend
        scenario = self._scenario
        persistence = self._resources.session.persistence
        if backend is None or scenario is None or persistence is None:
            raise P7Error("OPERATOR_HAZARD_LOOP_NOT_STARTED")
        geometry = backend.observe_geometry()
        sample = persistence.source.last_snapshot_sample
        if sample is None:
            raise P7Error("HAZARD_FRAME_PERSISTENCE_SAMPLE_REQUIRED")
        if geometry is None:
            if self._last is not None:
                scenario.abort(
                    replace(
                        self._last,
                        timestamp=sample.timestamp,
                        driver_input=driver_input,
                    ),
                    "OBSERVATION_STREAM_ENDED",
                    HazardOutcome.UNOBSERVABLE,
                )
            return False
        observation = replace(
            geometry,
            timestamp=sample.timestamp,
            driver_input=driver_input,
            input_source="INJECTED_TEST_DRIVER_INPUT",
        )
        self._last = observation
        if scenario.state is HazardState.IDLE:
            if not scenario.arm(observation):
                raise P7Error("APPROVED_HAZARD_DID_NOT_ARM")
        else:
            scenario.update(observation)
        self._tick_count += 1
        if scenario.termination_requested and self._rescue_status == "NOT_REQUESTED":
            self._request_safe_shutdown(observation)
        return scenario.state not in (HazardState.COMPLETED, HazardState.ABORTED)

    def finalize(self, reason: str, error: BaseException | None = None) -> None:
        if self._finalized:
            return
        scenario = self._scenario
        if (
            scenario is not None
            and self._last is not None
            and scenario.state not in (HazardState.COMPLETED, HazardState.ABORTED)
        ):
            try:
                scenario.abort(
                    replace(
                        self._last,
                        timestamp=(
                            self._resources.session.persistence.source.last_snapshot_sample.timestamp
                            if self._resources.session.persistence is not None
                            and self._resources.session.persistence.source.last_snapshot_sample
                            is not None
                            else self._last.timestamp
                        ),
                    ),
                    reason
                    if error is None
                    else f"EXECUTION_ERROR:{type(error).__name__}:{error}",
                    HazardOutcome.UNOBSERVABLE,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as abort_error:
                if error is None:
                    error = abort_error
                else:
                    error.add_note(
                        "Hazard abort logging failure: "
                        f"{type(abort_error).__name__}:{abort_error}"
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
                error.add_note(
                    "Hazard actor cleanup failure: "
                    f"{type(cleanup_error).__name__}:{cleanup_error}"
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
                error.add_note(str(cleanup_error))
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
            "execution_status": (
                "FAILED_OFFLINE" if error is not None else "EXECUTED_OFFLINE"
            ),
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
            "visibility_status": "FIRST_VISIBLE_FRAME_UNVERIFIED",
            "first_displayed_frame": None,
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
                    "NORMAL_COMPLETION"
                    if error is None
                    else f"FAILURE:{type(error).__name__}"
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
        if persistence is not None:
            persistence.flush_events()
        self._rescue_status = "REQUESTED"
        services.emit(
            "hazard_safe_shutdown",
            {"status": self._rescue_status, "physical_stop_success": None},
        )
        try:
            self._plan.safety_owner.request_safe_shutdown(observation.timestamp)
        except RuntimeError:
            self._rescue_status = "FAILED"
            services.emit(
                "hazard_safe_shutdown",
                {"status": self._rescue_status, "physical_stop_success": None},
            )
            raise
        self._rescue_status = "RETURNED_NOT_PHYSICAL_SUCCESS"
        services.emit(
            "hazard_safe_shutdown",
            {"status": self._rescue_status, "physical_stop_success": None},
        )
