from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

import carla

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = (
    JsonScalar | carla.Transform | list["JsonValue"] | dict[str, "JsonValue"]
)


class NativeTrafficManagerDiagnosticsSchemaError(TypeError):
    def __init__(self, field: str, requirement: str) -> None:
        self.field = field
        self.requirement = requirement
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"native TM diagnostic {self.field} {self.requirement}"


class NativeRecordMapping(Protocol):
    def get(self, key: str, default: JsonValue = None) -> JsonValue: ...
    def items(self): ...


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    event_sequence: int
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ValidatedSnapshot:
    selected_actor: int
    enabled: bool
    capacity: int
    trace_session: int
    loss: dict[str, int]
    records: tuple[ValidatedRecord, ...]

    def receipt(self, receipt_id: int, phase: str) -> dict[str, JsonValue]:
        return {
            "receipt_id": receipt_id,
            "phase": phase,
            "selected_actor": self.selected_actor,
            "enabled": self.enabled,
            "capacity": self.capacity,
            "trace_session": self.trace_session,
            "record_count": len(self.records),
            **self.loss,
        }


def validate_snapshot(
    raw: Mapping[str, JsonValue],
    phase: str,
    actor_id: int,
    capacity: int,
    trace_session: int | None,
    previous_loss: Mapping[str, int],
) -> ValidatedSnapshot:
    records = raw.get("records")
    if not isinstance(records, list):
        raise NativeTrafficManagerDiagnosticsSchemaError("records", "must be a list")
    selected_actor = _integer(raw, "selected_actor", minimum=0)
    actual_capacity = _integer(raw, "capacity", minimum=1)
    actual_session = _integer(raw, "trace_session", minimum=1)
    enabled = _boolean(raw, "enabled")
    expected_enabled = phase != "final_disabled"
    if selected_actor != actor_id:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "selected_actor", f"must equal {actor_id}"
        )
    if actual_capacity != capacity:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "capacity", f"must equal {capacity}"
        )
    if trace_session is not None and actual_session != trace_session:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "trace_session", f"must remain {trace_session}"
        )
    if enabled is not expected_enabled:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "enabled", f"must be {expected_enabled} during {phase}"
        )
    loss = {}
    for name in ("overwritten", "contention_dropped", "internal_dropped"):
        value = _integer(raw, name, minimum=0)
        if value < previous_loss[name]:
            raise NativeTrafficManagerDiagnosticsSchemaError(name, "must not decrease")
        loss[name] = value
    validated = tuple(_record(record, actor_id, actual_session) for record in records)
    sequences = [record.event_sequence for record in validated]
    if len(sequences) != len(set(sequences)):
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "event_sequence", "must be unique within a snapshot"
        )
    return ValidatedSnapshot(
        selected_actor, enabled, actual_capacity, actual_session, loss, validated
    )


def _record(raw: JsonValue, actor_id: int, trace_session: int) -> ValidatedRecord:
    if not isinstance(raw, dict):
        raise NativeTrafficManagerDiagnosticsSchemaError("record", "must be a mapping")
    if _integer(raw, "actor_id", minimum=0) != actor_id:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "record.actor_id", f"must equal {actor_id}"
        )
    if _integer(raw, "trace_session", minimum=1) != trace_session:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "record.trace_session", f"must equal {trace_session}"
        )
    sequence = _integer(raw, "event_sequence", minimum=1)
    _integer(raw, "native_steady_ns", minimum=0)
    _string(raw, "event")
    _string(raw, "reason")
    return ValidatedRecord(sequence, dict(raw))


def _integer(values: Mapping[str, JsonValue], name: str, *, minimum: int) -> int:
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NativeTrafficManagerDiagnosticsSchemaError(
            name, f"must be an integer >= {minimum}"
        )
    return value


def _boolean(values: Mapping[str, JsonValue], name: str) -> bool:
    value = values.get(name)
    if not isinstance(value, bool):
        raise NativeTrafficManagerDiagnosticsSchemaError(name, "must be a boolean")
    return value


def _string(values: Mapping[str, JsonValue], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise NativeTrafficManagerDiagnosticsSchemaError(name, "must be a string")
    return value
