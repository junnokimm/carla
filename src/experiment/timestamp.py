from __future__ import annotations

import math
from dataclasses import dataclass


class TimestampValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HostClockTimestamp:
    """Host readings for one observation record.

    ``monotonic_ns`` is nanoseconds from the host monotonic clock's unspecified
    origin. ``utc_ns`` is nanoseconds from the Unix epoch in UTC. The readings
    retain their separate clock domains and do not assert exact synchronization.
    """

    monotonic_ns: int
    utc_ns: int

    def __post_init__(self) -> None:
        if self.monotonic_ns < 0:
            raise TimestampValidationError("monotonic_ns must be non-negative")
        if self.utc_ns < 0:
            raise TimestampValidationError("utc_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class CarlaSnapshotTimestamp:
    """Time fields read from one CARLA world snapshot.

    ``simulation_seconds`` is elapsed simulation time in seconds since the
    current CARLA episode began. ``frame`` is CARLA's non-negative simulation
    frame number. The host monotonic nanosecond bounds bracket snapshot
    acquisition; they do not claim that the snapshot equals either host time.
    """

    simulation_seconds: float
    frame: int
    host_capture_started_monotonic_ns: int
    host_capture_completed_monotonic_ns: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.simulation_seconds) or self.simulation_seconds < 0:
            raise TimestampValidationError(
                "simulation_seconds must be finite and non-negative"
            )
        if self.frame < 0:
            raise TimestampValidationError("frame must be non-negative")
        if self.host_capture_started_monotonic_ns < 0:
            raise TimestampValidationError(
                "host_capture_started_monotonic_ns must be non-negative"
            )
        if self.host_capture_completed_monotonic_ns < 0:
            raise TimestampValidationError(
                "host_capture_completed_monotonic_ns must be non-negative"
            )
        if (
            self.host_capture_completed_monotonic_ns
            < self.host_capture_started_monotonic_ns
        ):
            raise TimestampValidationError(
                "host capture completion must not precede capture start"
            )


@dataclass(frozen=True, slots=True)
class ExternalClockTimestamp:
    """Uninterpreted timestamp supplied by an external device.

    ``raw_value`` preserves the device value verbatim. ``unit`` is optional
    because a device protocol may not yet define one. No host-clock mapping or
    synchronization accuracy is implied.
    """

    source: str
    raw_value: str
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise TimestampValidationError("source must not be blank")
        if not self.raw_value.strip():
            raise TimestampValidationError("raw_value must not be blank")
        if self.unit is not None and not self.unit.strip():
            raise TimestampValidationError("unit must not be blank when provided")


@dataclass(frozen=True, slots=True)
class TimestampEnvelope:
    """Common timestamp contract for an observation or research event.

    Host, CARLA, and external clocks remain explicit fields. Missing clock data
    remains ``None`` or an empty tuple rather than receiving an invented value.
    """

    host: HostClockTimestamp
    carla_snapshot: CarlaSnapshotTimestamp | None = None
    external_clocks: tuple[ExternalClockTimestamp, ...] = ()


__all__ = [
    "CarlaSnapshotTimestamp",
    "ExternalClockTimestamp",
    "HostClockTimestamp",
    "TimestampEnvelope",
    "TimestampValidationError",
]
