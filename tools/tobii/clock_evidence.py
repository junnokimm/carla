from __future__ import annotations

import json
import platform
import sys
import time

from tools.tobii.sdk import Sdk


def clock_metadata() -> dict:
    clocks = {}
    for name in ("monotonic", "perf_counter", "time"):
        info = time.get_clock_info(name)
        clocks[name] = {"implementation": info.implementation, "resolution_s": info.resolution,
                        "monotonic": info.monotonic, "adjustable": info.adjustable,
                        "unit": "ns via *_ns()", "origin": "Unix epoch" if name == "time" else "arbitrary; do not interchange clocks"}
    return {"python": sys.version, "platform": platform.platform(), "clocks": clocks,
            "sdk_system": {"name": "tobii_research.get_system_time_stamp", "unit": "us",
                           "origin": "SDK host arbitrary origin, not UTC", "resolution": "NOT_EXPOSED_BY_SDK"},
            "device_timestamp": {"name": "device_time_stamp", "unit": "us", "origin": "device arbitrary origin"},
            "clock_accuracy": "NOT_VALIDATED"}


def clock_pair(sdk: Sdk, phase: str) -> dict:
    before = time.monotonic_ns()
    perf_before = time.perf_counter_ns()
    sdk_time = sdk.get_system_time_stamp()
    perf_after = time.perf_counter_ns()
    after = time.monotonic_ns()
    return {"record_type": "clock_pair", "phase": phase, "sdk_system_us": sdk_time,
            "host_before_ns": before, "host_after_ns": after,
            "host_perf_counter_before_ns": perf_before, "host_perf_counter_after_ns": perf_after,
            "bridge_policy": "RAW_BRACKETS_ONLY_NO_SHARED_ORIGIN_ASSUMPTION"}


def metadata_record() -> dict:
    return {"record_type": "clock_metadata", "clock_info_json": json.dumps(clock_metadata())}
