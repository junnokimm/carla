from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import assert_never

from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.p7.types import Json
from src.vehicle import VehicleObservation


class ShutdownBrakeCommandOutcome(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    RETURNED = "RETURNED"
    RAISED = "RAISED"


class ShutdownBrakeObservationStatus(StrEnum):
    NOT_CAPTURED = "NOT_CAPTURED"
    CAPTURED_WITHOUT_COMMAND = "CAPTURED_WITHOUT_COMMAND"
    CAPTURED_AFTER_COMMAND_CALL = "CAPTURED_AFTER_COMMAND_CALL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ShutdownExceptionDetails:
    type: str
    message: str

    @classmethod
    def from_exception(cls, error: BaseException) -> ShutdownExceptionDetails:
        return cls(type(error).__name__, str(error))

    def manifest(self) -> dict[str, Json]:
        return {"type": self.type, "message": self.message}


@dataclass(frozen=True, slots=True)
class ShutdownBrakeObservation:
    capture_status: ShutdownBrakeObservationStatus
    capture_error: ShutdownExceptionDetails | None = None
    frame: int | None = None
    frame_relation: str = "UNKNOWN"
    speed_kmh: float | None = None
    steering: float | None = None
    throttle: float | None = None
    brake: float | None = None

    def manifest(self) -> dict[str, Json]:
        return {
            "capture_status": self.capture_status.value,
            "capture_error": (
                None if self.capture_error is None else self.capture_error.manifest()
            ),
            "frame": self.frame,
            "frame_relation": self.frame_relation,
            "speed_kmh": self.speed_kmh,
            "steering": self.steering,
            "throttle": self.throttle,
            "brake": self.brake,
            "causal_application_proof": False,
        }


@dataclass(frozen=True, slots=True)
class ShutdownBrakeOutcome:
    command_call_outcome: ShutdownBrakeCommandOutcome
    requested_throttle: float | None = None
    requested_brake: float | None = None
    requested_steer: float | None = None
    command_started_host: HostClockTimestamp | None = None
    command_completed_host: HostClockTimestamp | None = None
    command_exception: ShutdownExceptionDetails | None = None
    shutdown_phase_exception: ShutdownExceptionDetails | None = None
    completion_timestamp_exception: ShutdownExceptionDetails | None = None
    physical_braking: str = "NOT_VERIFIED"
    observation: ShutdownBrakeObservation = ShutdownBrakeObservation(
        ShutdownBrakeObservationStatus.NOT_CAPTURED
    )

    @classmethod
    def not_attempted(cls) -> ShutdownBrakeOutcome:
        return cls(ShutdownBrakeCommandOutcome.NOT_ATTEMPTED)

    def with_observation(
        self,
        observation: VehicleObservation | None,
        capture_error: BaseException | None,
    ) -> ShutdownBrakeOutcome:
        if observation is None:
            captured = ShutdownBrakeObservation(
                ShutdownBrakeObservationStatus.UNAVAILABLE,
                capture_error=(
                    None
                    if capture_error is None
                    else ShutdownExceptionDetails.from_exception(capture_error)
                ),
            )
        else:
            snapshot = observation.timestamp.carla_snapshot
            state = observation.state
            match self.command_call_outcome:
                case ShutdownBrakeCommandOutcome.NOT_ATTEMPTED:
                    capture_status = (
                        ShutdownBrakeObservationStatus.CAPTURED_WITHOUT_COMMAND
                    )
                case (
                    ShutdownBrakeCommandOutcome.RETURNED
                    | ShutdownBrakeCommandOutcome.RAISED
                ):
                    capture_status = (
                        ShutdownBrakeObservationStatus.CAPTURED_AFTER_COMMAND_CALL
                    )
                case unreachable:
                    assert_never(unreachable)
            captured = ShutdownBrakeObservation(
                capture_status,
                frame=None if snapshot is None else snapshot.frame,
                speed_kmh=state.speed_kmh,
                steering=state.steering,
                throttle=state.throttle,
                brake=state.brake,
            )
        return replace(self, observation=captured)

    def manifest(self) -> dict[str, Json]:
        return {
            "command_call_outcome": self.command_call_outcome.value,
            "requested_control": {
                "throttle": self.requested_throttle,
                "brake": self.requested_brake,
                "steer": self.requested_steer,
            },
            "command_started_host": _host_manifest(self.command_started_host),
            "command_completed_host": _host_manifest(self.command_completed_host),
            "command_exception": (
                None
                if self.command_exception is None
                else self.command_exception.manifest()
            ),
            "shutdown_phase_exception": (
                None
                if self.shutdown_phase_exception is None
                else self.shutdown_phase_exception.manifest()
            ),
            "completion_timestamp_exception": (
                None
                if self.completion_timestamp_exception is None
                else self.completion_timestamp_exception.manifest()
            ),
            "physical_braking": self.physical_braking,
            "observation": self.observation.manifest(),
        }


def host_timestamp(timestamp: TimestampEnvelope | None) -> HostClockTimestamp | None:
    return None if timestamp is None else timestamp.host


def _host_manifest(timestamp: HostClockTimestamp | None) -> dict[str, Json] | None:
    if timestamp is None:
        return None
    return {
        "monotonic_ns": timestamp.monotonic_ns,
        "utc_ns": timestamp.utc_ns,
    }
