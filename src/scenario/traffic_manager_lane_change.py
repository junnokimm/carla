from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from src.p8.tm_runtime_attestation import RuntimeAttestation
from src.scenario.lane_change_execution import (
    LaneChangeSubmission,
    LaneChangeSubmissionStatus,
    StaleLaneChangeGenerationError,
)


class LaneChangeActor(Protocol):
    id: int


class LaneChangeActorList(Protocol):
    def filter(self, pattern: str) -> list[LaneChangeActor]: ...


class LaneChangeWorld(Protocol):
    def get_actors(self) -> LaneChangeActorList: ...


class LaneChangeTrafficManager(Protocol):
    def force_lane_change(self, actor: LaneChangeActor, direction: bool) -> None: ...


class LaneChangeOwnershipVerifier(Protocol):
    def verify_owned(self, attestation: RuntimeAttestation) -> None: ...


class TrafficManagerLaneChangeExecutor:
    def __init__(
        self,
        actor: LaneChangeActor,
        traffic_manager: LaneChangeTrafficManager,
        world: LaneChangeWorld | None,
        ownership_verifier: LaneChangeOwnershipVerifier | None,
        runtime_attestation: RuntimeAttestation | None,
    ) -> None:
        self._actor = actor
        self._traffic_manager = traffic_manager
        self._world = world
        self._ownership_verifier = ownership_verifier
        self._runtime_attestation = runtime_attestation
        self._generation = 0
        self._active = False
        self._in_flight: LaneChangeSubmission | None = None
        self._consumed_request_ids: set[str] = set()

    @property
    def activation_generation(self) -> int:
        return self._generation

    @property
    def in_flight(self) -> LaneChangeSubmission | None:
        return self._in_flight

    def activate(self) -> None:
        self._generation += 1
        self._active = True

    def begin_deactivation(self) -> LaneChangeSubmission | None:
        self._generation += 1
        self._active = False
        return self._in_flight

    def confirm_deactivation(self) -> LaneChangeSubmission | None:
        submission = self._in_flight
        self._in_flight = None
        return submission

    def invalidate(self) -> LaneChangeSubmission | None:
        return self.begin_deactivation()

    def interrupt(self) -> LaneChangeSubmission | None:
        return self._in_flight

    def submit(
        self,
        request_id: str,
        activation_generation: int,
        *,
        right_lane_change: bool,
    ) -> LaneChangeSubmission:
        if request_id in self._consumed_request_ids:
            return self._result(
                request_id,
                activation_generation,
                right_lane_change,
                LaneChangeSubmissionStatus.REJECTED,
                "REQUEST_ID_ALREADY_CONSUMED",
            )
        self._consumed_request_ids.add(request_id)
        if activation_generation != self._generation:
            raise StaleLaneChangeGenerationError(
                self._generation,
                activation_generation,
            )
        if not self._active:
            return self._result(
                request_id,
                activation_generation,
                right_lane_change,
                LaneChangeSubmissionStatus.REJECTED,
                "NOA_INACTIVE",
            )
        if self._in_flight is not None:
            return self._result(
                request_id,
                activation_generation,
                right_lane_change,
                LaneChangeSubmissionStatus.REJECTED,
                "LANE_CHANGE_ALREADY_IN_FLIGHT",
            )
        inventory_reason = self._inventory_rejection()
        if inventory_reason is not None:
            return self._result(
                request_id,
                activation_generation,
                right_lane_change,
                LaneChangeSubmissionStatus.BLOCKED,
                inventory_reason,
            )
        if self._ownership_verifier is None or self._runtime_attestation is None:
            return self._result(
                request_id,
                activation_generation,
                right_lane_change,
                LaneChangeSubmissionStatus.BLOCKED,
                "TM_RUNTIME_OWNERSHIP_UNVERIFIED",
            )
        self._ownership_verifier.verify_owned(self._runtime_attestation)
        submission = self._result(
            request_id,
            activation_generation,
            right_lane_change,
            LaneChangeSubmissionStatus.SUBMITTED,
            None,
        )
        self._in_flight = submission
        try:
            self._traffic_manager.force_lane_change(
                self._actor,
                right_lane_change,
            )
        except RuntimeError as error:
            uncertain = replace(
                submission,
                status=LaneChangeSubmissionStatus.UNVERIFIED,
                reason=f"{type(error).__name__}:{error}",
            )
            self._in_flight = uncertain
            return uncertain
        return submission

    def complete(self, generation: int) -> LaneChangeSubmission:
        submission = self._require_generation(generation)
        completed = replace(submission, status=LaneChangeSubmissionStatus.COMPLETED)
        self._in_flight = None
        return completed

    def _require_generation(self, generation: int) -> LaneChangeSubmission:
        submission = self._in_flight
        if (
            submission is None
            or submission.generation != generation
            or generation != self._generation
        ):
            raise StaleLaneChangeGenerationError(self._generation, generation)
        return submission

    def _inventory_rejection(self) -> str | None:
        if self._world is None:
            return "EMPTY_REHEARSAL_INVENTORY_UNAVAILABLE"
        try:
            actors = self._world.get_actors()
            vehicles = list(actors.filter("vehicle.*"))
            prohibited = [
                actor
                for pattern in ("vehicle.*", "walker.*", "static.prop.*")
                for actor in actors.filter(pattern)
                if actor.id != self._actor.id
            ]
        except (AttributeError, RuntimeError):
            return "EMPTY_REHEARSAL_INVENTORY_FAILED"
        if len(vehicles) != 1 or vehicles[0].id != self._actor.id or prohibited:
            return "EMPTY_REHEARSAL_INVENTORY_REJECTED"
        return None

    @staticmethod
    def _result(
        request_id: str,
        generation: int,
        right_lane_change: bool,
        status: LaneChangeSubmissionStatus,
        reason: str | None,
    ) -> LaneChangeSubmission:
        return LaneChangeSubmission(
            request_id,
            generation,
            "RIGHT" if right_lane_change else "LEFT",
            status,
            reason,
        )
