from __future__ import annotations

import itertools
import json
import queue
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from typing import TypeAlias

from tools.tobii.clock_evidence import clock_pair, metadata_record
from tools.tobii.sdk import DeviceIdentity, Sdk, SdkCallback, SdkData, select_nano

WireValue: TypeAlias = str | int | float | bool | None | dict[str, str]
WireRecord: TypeAlias = dict[str, WireValue]
Emit: TypeAlias = Callable[[WireRecord], None]


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    serial: str
    seconds: float
    include_display_area: bool = False


def capture(sdk: Sdk, options: CaptureOptions, emit: Emit, *,
            stop_requested: threading.Event | None = None) -> str:
    tracker = select_nano(sdk, options.serial)
    identity = asdict(DeviceIdentity.from_tracker(tracker))
    emit({"record_type": "identity", "schema_version": 1, "device": identity,
          "synthetic": False, "calibration_status": "NOT_VERIFIED"})
    if options.include_display_area:
        try:
            area = tracker.get_display_area()
            emit({"record_type": "display_area", "status": "OK",
                  "serial_number": options.serial,
                  "display_area": {key: getattr(area, key) for key in
                                   ("top_left", "top_right", "bottom_left", "bottom_right")}})
        except Exception as error:  # noqa: BLE001 -- native SDK failures must leave capture available
            # Optional AOI binding must not prevent raw/monitor gaze collection.
            emit({"record_type": "display_area", "status": "UNAVAILABLE", "detail": str(error)})

    def emit_pair(phase: str) -> None:
        try:
            emit(clock_pair(sdk, phase))
        except Exception as error:  # noqa: BLE001 -- optional clock evidence must not stop gaze
            emit({"record_type": "clock_pair_error", "phase": phase, "detail": str(error)})

    emit(metadata_record())
    emit_pair("START")
    pending: queue.Queue[WireRecord] = queue.Queue(256)
    stopped = threading.Event()
    sequence = itertools.count()
    dropped = 0
    failure = ""
    last_notification = ""

    def on_gaze(raw: SdkData) -> None:
        nonlocal dropped
        host_ns, perf_ns, wall_ns = time.monotonic_ns(), time.perf_counter_ns(), time.time_ns()
        record: WireRecord = {
            "record_type": "gaze", "schema_version": 1, "synthetic": False,
            "source_sequence": next(sequence), "host_monotonic_ns": host_ns,
            "host_wall_time_ns": wall_ns, "device": identity,
            "host_perf_counter_ns": perf_ns,
            "calibration_status": "NOT_VERIFIED",
            "raw_sdk_json": json.dumps(raw, separators=(",", ":"), allow_nan=True),
        }
        try:
            pending.put_nowait(record)
        except queue.Full:
            dropped += 1

    def on_lost(raw: SdkData) -> None:
        nonlocal failure, last_notification
        failure, last_notification = "DISCONNECTED", json.dumps(raw)
        stopped.set()

    def on_error(raw: SdkData) -> None:
        nonlocal failure, last_notification
        failure, last_notification = "SDK_STREAM_ERROR", json.dumps(raw)
        stopped.set()

    subscriptions: list[tuple[str, SdkCallback]] = []
    count = 0
    try:
        for topic, callback in (
            (sdk.EYETRACKER_NOTIFICATION_CONNECTION_LOST, on_lost),
            (sdk.EYETRACKER_STREAM_ERRORS, on_error),
            (sdk.EYETRACKER_GAZE_DATA, on_gaze),
        ):
            tracker.subscribe_to(topic, callback, as_dictionary=True)
            subscriptions.append((topic, callback))
        deadline = time.monotonic() + options.seconds
        next_pair = time.monotonic() + 1.0
        while not stopped.is_set() and time.monotonic() < deadline:
            if stop_requested is not None and stop_requested.is_set():
                break
            if time.monotonic() >= next_pair:
                emit_pair("MIDDLE")
                next_pair = time.monotonic() + 1.0
            try:
                record = pending.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            emit(record)
            count += 1
    finally:
        try:
            with ExitStack() as cleanup:
                for topic, callback in subscriptions:
                    cleanup.callback(tracker.unsubscribe_from, topic, callback)
        finally:
            while True:
                try:
                    record = pending.get_nowait()
                except queue.Empty:
                    break
                emit(record)
                count += 1
            try:
                emit_pair("END")
            except (RuntimeError, OSError, ValueError):
                failure = failure or "CLOCK_PAIR_FAILED"
            result = failure or ("RAW_CAPTURED" if count else "NO_SAMPLES")
            if dropped and not failure:
                result = "QUEUE_OVERFLOW"
            emit({"record_type": "status", "status": result, "sample_count": count,
                  "dropped_count": dropped, "notification_raw_json": last_notification,
                  "calibration_status": "NOT_VERIFIED", "aoi_validated": False})
    return result
