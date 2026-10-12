from __future__ import annotations

import json
import math
from dataclasses import dataclass

from src.p6.models import ClockKind, EyeDatum, JsonScalar, RawGazeSample, Validity
from tools.tobii.sdk import TobiiError


@dataclass(frozen=True, slots=True)
class TobiiEnvelope:
    sample: RawGazeSample
    host_monotonic_ns: int
    host_wall_time_ns: int


class TobiiSampleDecoder:
    def __init__(self) -> None:
        self._last_device: int | None = None
        self._last_system: int | None = None

    def decode(self, line: str) -> TobiiEnvelope:
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("schema_version") != 1:
                raise TobiiError("INVALID_RECORD", "Expected schema version 1")
            if record.get("record_type") != "gaze" or record.get("synthetic") is not False:
                raise TobiiError("INVALID_RECORD", "Expected explicitly real gaze")
            device = record["device"]
            if not isinstance(device, dict) or device.get("model") != "Tobii Pro Nano":
                raise TobiiError("MODEL_MISMATCH", "Expected Tobii Pro Nano identity")
            identity = {}
            for field in ("model", "serial_number", "address", "device_name", "firmware_version"):
                if not isinstance(device[field], str):
                    raise TobiiError("INVALID_RECORD", field)
                identity[field] = device[field]
            raw_json = record["raw_sdk_json"]
            if not isinstance(raw_json, str):
                raise TobiiError("INVALID_RECORD", "raw_sdk_json")
            raw = json.loads(raw_json)
            if not isinstance(raw, dict):
                raise TobiiError("INVALID_RECORD", "SDK dictionary required")
            device_time = _integer(raw["device_time_stamp"])
            system_time = _integer(raw["system_time_stamp"])
            calibration = record["calibration_status"]
            if not isinstance(calibration, str):
                raise TobiiError("INVALID_RECORD", "calibration_status")
            left = _eye(raw["left_gaze_point_on_display_area"], raw["left_gaze_point_validity"])
            right = _eye(raw["right_gaze_point_on_display_area"], raw["right_gaze_point_validity"])
            metadata = {
                **identity, "provider": "tobii_pro_sdk", "synthetic": False,
                "device_timestamp_raw": device_time, "system_timestamp_raw": system_time,
                "raw_sdk_json": raw_json, "calibration_status": calibration,
                "sdk_callback_host_monotonic_ns": _integer(record["host_monotonic_ns"]),
                "sdk_callback_host_wall_time_ns": _integer(record["host_wall_time_ns"]),
                "left_validity": raw["left_gaze_point_validity"],
                "right_validity": raw["right_gaze_point_validity"],
                "duplicate_device_timestamp": device_time == self._last_device,
                "duplicate_system_timestamp": system_time == self._last_system,
                "backward_device_timestamp": self._last_device is not None and device_time < self._last_device,
                "backward_system_timestamp": self._last_system is not None and system_time < self._last_system,
            }
            if "host_perf_counter_ns" in record:
                metadata["sdk_callback_host_perf_counter_ns"] = _integer(record["host_perf_counter_ns"])
            envelope = TobiiEnvelope(
                RawGazeSample(_integer(record["source_sequence"]), system_time, "us",
                              ClockKind.SYSTEM, left, right, metadata),
                _integer(record["host_monotonic_ns"]), _integer(record["host_wall_time_ns"]),
            )
            self._last_device = device_time
            self._last_system = system_time
            return envelope
        except (KeyError, TypeError, ValueError) as error:
            raise TobiiError("INVALID_RECORD", str(error)) from error


def _integer(value: JsonScalar) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TobiiError("INVALID_RECORD", "integer timestamp/sequence required")
    return value


def _eye(point: list[float], validity: int | bool) -> EyeDatum:
    if not isinstance(point, list) or len(point) != 2:
        raise TobiiError("INVALID_RECORD", "two SDK gaze coordinates required")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in point):
        raise TobiiError("INVALID_RECORD", "numeric SDK coordinates required")
    if not isinstance(validity, (int, bool)) or validity not in (0, 1):
        raise TobiiError("INVALID_RECORD", "SDK validity must be 0 or 1")
    x, y = point
    reason = None
    if validity != 1:
        reason = "SDK_INVALID"
    elif not math.isfinite(x) or not math.isfinite(y):
        reason = "NONFINITE"
    elif not (0 <= x <= 1 and 0 <= y <= 1):
        reason = "OUTSIDE_DISPLAY_AREA"
    return EyeDatum(x if math.isfinite(x) else None, y if math.isfinite(y) else None,
                    Validity.VALID if reason is None else Validity.INVALID, reason)
