from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TextIO

import carla

from src.experiment.timestamp import HostClockTimestamp
from src.p8.native_tm_diagnostics_schema import (
    NativeTrafficManagerDiagnosticsSchemaError,
)
from src.p8.native_tm_diagnostics_types import (
    JsonRow,
    JsonValue,
    NativeTrafficManagerDiagnosticsContext,
)
from src.vehicle import VehicleObservation


class NativeTrafficManagerDiagnosticsJsonl:
    def __init__(
        self,
        stream: TextIO,
        context: NativeTrafficManagerDiagnosticsContext,
        actor_id: int,
    ) -> None:
        self._stream = stream
        self._context = context
        self._actor_id = actor_id

    def common(
        self,
        kind: str,
        monotonic_ns: int | None = None,
        utc_ns: int | None = None,
    ) -> JsonRow:
        host_mono = (
            self._context.host_monotonic_ns() if monotonic_ns is None else monotonic_ns
        )
        host_utc = self._context.host_utc_ns() if utc_ns is None else utc_ns
        session_elapsed_s = self._context.session_clock.project_elapsed_seconds(
            HostClockTimestamp(host_mono, host_utc)
        )
        return {
            "kind": kind,
            "participant_id": self._context.participant_id,
            "run_id": self._context.run_id,
            "ego_actor_id": self._actor_id,
            "originating_stage": self._context.originating_stage,
            "measurement_phase": self._context.measurement_phase(),
            "host_monotonic_ns": host_mono,
            "host_utc_ns": host_utc,
            "session_elapsed_s": session_elapsed_s,
            "project_elapsed_seconds": session_elapsed_s,
        }

    def receipt_timing(
        self,
        begin_mono: int,
        begin_utc: int,
        end_mono: int,
        end_utc: int,
        observation: VehicleObservation | None,
    ) -> JsonRow:
        snapshot = None if observation is None else observation.timestamp.carla_snapshot
        return {
            "read_begin_host_monotonic_ns": begin_mono,
            "read_begin_host_utc_ns": begin_utc,
            "read_end_host_monotonic_ns": end_mono,
            "read_end_host_utc_ns": end_utc,
            "project_elapsed_receipt_seconds": self._context.session_clock.project_elapsed_seconds(
                HostClockTimestamp(end_mono, end_utc)
            ),
            "carla_frame": None if snapshot is None else snapshot.frame,
            "carla_simulation_seconds": None
            if snapshot is None
            else snapshot.simulation_seconds,
            "clock_mapping": "READ_RECEIPT_ONLY_NO_NATIVE_CLOCK_MAPPING",
        }

    def write(self, row: Mapping[str, JsonValue]) -> None:
        self._stream.write(
            json.dumps(
                row,
                default=_json_default,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        self._stream.flush()


def _json_default(value: carla.Transform) -> JsonValue:
    if not isinstance(value, carla.Transform):
        raise NativeTrafficManagerDiagnosticsSchemaError(
            "native payload", f"contains unsupported {type(value).__name__}"
        )
    return {
        "location": {
            "x": float(value.location.x),
            "y": float(value.location.y),
            "z": float(value.location.z),
        },
        "rotation": {
            "pitch": float(value.rotation.pitch),
            "yaw": float(value.rotation.yaw),
            "roll": float(value.rotation.roll),
        },
    }
