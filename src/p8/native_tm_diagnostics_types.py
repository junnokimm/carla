from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

import carla

from src.experiment.session_clock import SessionClock

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = (
    JsonScalar | carla.Transform | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonRow: TypeAlias = dict[str, JsonValue]


def native_tm_diagnostic_path(output_dir: Path, stage: str, actor_id: int) -> Path:
    return output_dir / f"native_tm_diagnostics.{stage}.ego-{actor_id}.jsonl"


@dataclass(frozen=True, slots=True)
class NativeTrafficManagerDiagnosticsConfigError(ValueError):
    field: str
    requirement: str

    def __str__(self) -> str:
        return f"{self.field} {self.requirement}"


@dataclass(frozen=True, slots=True)
class NativeTrafficManagerDiagnosticsConfig:
    capacity: int = 1024
    interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        if isinstance(self.capacity, bool) or not 1 <= self.capacity <= 4096:
            raise NativeTrafficManagerDiagnosticsConfigError(
                "capacity", "must be within [1, 4096]"
            )
        if not 0.05 <= self.interval_seconds <= 5.0:
            raise NativeTrafficManagerDiagnosticsConfigError(
                "interval_seconds", "must be finite and within [0.05, 5.0]"
            )


@dataclass(frozen=True, slots=True)
class NativeTrafficManagerDiagnosticsContext:
    participant_id: str
    run_id: str
    originating_stage: str
    session_clock: SessionClock
    measurement_phase: Callable[[], str]
    host_monotonic_ns: Callable[[], int] = time.monotonic_ns
    host_utc_ns: Callable[[], int] = time.time_ns


@dataclass(frozen=True, slots=True)
class NativeTrafficManagerDiagnosticsStatus:
    sidecar_path: str
    actor_id: int
    originating_stage: str
    current_measurement_phase: str
    trace_session: int | None
    coverage_status: str
    coverage_complete: bool
    records_written: int
    overwritten: int
    contention_dropped: int
    internal_dropped: int
    first_error: str | None
    cleanup_errors: tuple[str, ...] = ()

    def payload(self) -> dict[str, JsonValue]:
        return {
            "sidecar_path": self.sidecar_path,
            "actor_id": self.actor_id,
            "originating_stage": self.originating_stage,
            "current_measurement_phase": self.current_measurement_phase,
            "trace_session": self.trace_session,
            "coverage_status": self.coverage_status,
            "coverage_complete": self.coverage_complete,
            "records_written": self.records_written,
            "overwritten": self.overwritten,
            "contention_dropped": self.contention_dropped,
            "internal_dropped": self.internal_dropped,
            "first_error": self.first_error,
            "cleanup_errors": list(self.cleanup_errors),
        }
