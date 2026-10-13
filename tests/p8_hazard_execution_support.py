from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from src.experiment.automation_interaction import DriverInput
from src.experiment.context import Module1Condition, Module2Condition
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.p7.config import HazardConfig
from src.p7.ownership import ActorRole, OwnedActors
from src.p7.types import HazardObservation
from src.p8.model import Mode, Stage
from src.p8.operator_controller import OperatorController
from src.p8.operator_hazard_contracts import (
    HazardCleanupEvidence,
    HazardExecutionPlan,
    HazardExecutionResources,
    HazardPolicyApproval,
)
from src.p8.operator_model import DevOutcome, RegistrationRequest
from tests.test_p8_operator_backend import (
    RunnerEvidence,
    StrictClock,
    advance_to,
    runtime_config,
)


class OfflineObservationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class OwnedFakeActor:
    def __init__(self, actor_id: int) -> None:
        self.id = actor_id
        self.destroy_count = 0

    def destroy(self) -> bool:
        self.destroy_count += 1
        return True


class OfflineActors:
    def __init__(self, owned: OwnedActors) -> None:
        self.owned = owned
        self.prepare_count = 0
        self.reveal_count = 0

    def prepare(self, timestamp: TimestampEnvelope) -> None:
        self.prepare_count += 1

    def begin_reveal(self, timestamp: TimestampEnvelope) -> None:
        self.reveal_count += 1


class OfflineBackend:
    def __init__(
        self,
        observations: list[HazardObservation],
        owned: OwnedActors,
        actors: OfflineActors,
        clock: StrictClock,
        *,
        error_after: int | None = None,
        observation_failure: BaseException | None = None,
        cleanup_evidence: HazardCleanupEvidence | None = None,
        cleanup_failure: RuntimeError | None = None,
        failure_action: Callable[[], None] | None = None,
    ) -> None:
        self.observations = observations
        self.owned = owned
        self._actors = actors
        self.clock = clock
        self.error_after = error_after
        self.observation_failure = observation_failure or OfflineObservationError(
            "offline observation failed"
        )
        self.cleanup_evidence = cleanup_evidence
        self.cleanup_failure = cleanup_failure
        self.failure_action = failure_action
        self.read_count = 0
        self.cleanup_count = 0

    @property
    def actors(self) -> OfflineActors:
        return self._actors

    def observe_geometry(self) -> HazardObservation | None:
        if self.error_after is not None and self.read_count == self.error_after:
            if self.failure_action is not None:
                self.failure_action()
            raise self.observation_failure
        self.read_count += 1
        if not self.observations:
            return None
        return self.observations.pop(0)

    def cleanup(self) -> HazardCleanupEvidence:
        self.cleanup_count += 1
        if self.cleanup_failure is not None:
            raise self.cleanup_failure
        failures = self.owned.close()
        return self.cleanup_evidence or HazardCleanupEvidence(
            failures, tuple(self.owned.actors)
        )


class SafetyOwner:
    def __init__(self, failure: RuntimeError | None = None) -> None:
        self.failure = failure
        self.requests: list[TimestampEnvelope] = []

    def request_safe_shutdown(self, timestamp: TimestampEnvelope) -> None:
        self.requests.append(timestamp)
        if self.failure is not None:
            raise self.failure


class OfflineBackendFactory:
    def __init__(
        self,
        backend: OfflineBackend,
        resources_seen: list[HazardExecutionResources],
        acquisition_failure: RuntimeError | None = None,
    ) -> None:
        self.backend = backend
        self.resources_seen = resources_seen
        self.acquisition_failure = acquisition_failure
        self.partial_cleanup_count = 0

    def acquire(self, resources: HazardExecutionResources) -> OfflineBackend:
        self.resources_seen.append(resources)
        if self.acquisition_failure is not None:
            raise self.acquisition_failure
        return self.backend

    def cleanup_partial(self) -> HazardCleanupEvidence:
        self.partial_cleanup_count += 1
        return self.backend.cleanup()


def observation(
    station: float,
    *,
    lateral: float = 0.0,
    reveal: bool = False,
    complete: bool = False,
    collision_actor_id: int | None = None,
    driver_input: DriverInput | None = None,
) -> HazardObservation:
    return HazardObservation(
        TimestampEnvelope(HostClockTimestamp(0, 0)),
        ego_station_m=station,
        lead_station_m=12.0,
        lead_lateral_m=lateral,
        target_lane_id=-4,
        lead_complete=complete,
        reveal_clear=reveal,
        distance_m=30.0,
        closing_speed_mps=10.0,
        ego_speed_mps=10.0,
        collision_course=True,
        driver_input=DriverInput() if driver_input is None else driver_input,
        input_source="OFFLINE_TEST_RAW_INPUT",
        collision_actor_id=collision_actor_id,
    )


def controller_with_hazard(
    tmp_path: Path,
    clock: StrictClock,
    observations: list[HazardObservation],
    *,
    error_after: int | None = None,
    observation_failure: BaseException | None = None,
    cleanup_evidence: HazardCleanupEvidence | None = None,
    cleanup_failure: RuntimeError | None = None,
    m2_condition: Module2Condition = Module2Condition.MANUAL,
    acquisition_failure: RuntimeError | None = None,
    safety_failure: RuntimeError | None = None,
    failure_action: Callable[[], None] | None = None,
) -> tuple[
    OperatorController,
    RunnerEvidence,
    OfflineBackend,
    SafetyOwner,
    tuple[OwnedFakeActor, OwnedFakeActor],
    list[HazardExecutionResources],
]:
    actual_inputs = [
        value.driver_input if value.driver_input is not None else DriverInput()
        for value in observations
    ]
    evidence = RunnerEvidence(
        [], [], [], [DriverInput(), DriverInput(), *actual_inputs, DriverInput()]
    )
    input_count = 0

    def advance_hazard_frame() -> None:
        nonlocal input_count
        input_count += 1
        if input_count > 2:
            clock.advance(1.0)

    evidence.driver_input_action = advance_hazard_frame
    owned = OwnedActors(ego_id=42)
    lead = OwnedFakeActor(101)
    cargo = OwnedFakeActor(102)
    owned.add(lead, ActorRole.LEAD)
    owned.add(cargo, ActorRole.CARGO)
    actors = OfflineActors(owned)
    backend = OfflineBackend(
        observations,
        owned,
        actors,
        clock,
        error_after=error_after,
        observation_failure=observation_failure,
        cleanup_evidence=cleanup_evidence,
        cleanup_failure=cleanup_failure,
        failure_action=failure_action,
    )
    safety = SafetyOwner(safety_failure)
    resources_seen: list[HazardExecutionResources] = []

    plan = HazardExecutionPlan(
        HazardPolicyApproval(
            HazardConfig(),
            "TEST_APPROVED_POLICY",
            "TEST-APPROVAL-1",
            "INJECTED_FAKE_BACKEND_READY",
        ),
        OfflineBackendFactory(backend, resources_seen, acquisition_failure),
        safety,
    )
    runtime = replace(runtime_config(clock, evidence), hazard_execution=plan)
    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime,
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            "P7-OFFLINE",
            "p7-offline-run",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=m2_condition,
        )
    )
    return controller, evidence, backend, safety, (lead, cargo), resources_seen


def advance_to_nominal_m2(controller: OperatorController) -> None:
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline M1")
    for _ in range(4):
        controller.advance()
