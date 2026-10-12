from __future__ import annotations

import json

import pytest

from tests.test_p6_tobii_protocol import gaze_record
from tools.tobii.capture import CaptureOptions, capture
from tools.tobii.sdk import SdkCallback


class FakeStreamingTracker:
    model = "Tobii Pro Nano"
    serial_number = "FAKE-001"
    address = "fake://nano"
    device_name = "fixture"
    firmware_version = "v0"

    def __init__(self) -> None:
        self.callbacks: dict[str, SdkCallback] = {}
        self.removed: list[str] = []

    def subscribe_to(self, subscription_type: str, callback: SdkCallback,
                     as_dictionary: bool = False) -> None:
        assert as_dictionary
        self.callbacks[subscription_type] = callback
        if subscription_type == "gaze":
            callback(json.loads(gaze_record()["raw_sdk_json"]))
            self.callbacks["lost"]({"system_time_stamp": 999})

    def unsubscribe_from(self, subscription_type: str, callback: SdkCallback) -> None:
        assert self.callbacks[subscription_type] == callback
        self.removed.append(subscription_type)


class FakeStreamingSdk:
    EYETRACKER_GAZE_DATA = "gaze"
    EYETRACKER_NOTIFICATION_CONNECTION_LOST = "lost"
    EYETRACKER_STREAM_ERRORS = "errors"

    def __init__(self, tracker: FakeStreamingTracker) -> None:
        self.tracker = tracker
        self.clock = 0

    def find_all_eyetrackers(self) -> tuple[FakeStreamingTracker, ...]:
        return (self.tracker,)

    def get_system_time_stamp(self) -> int:
        self.clock += 100
        return self.clock


def test_disconnect_saves_partial_raw_sample_and_unsubscribes() -> None:
    tracker = FakeStreamingTracker()
    rows: list[dict] = []

    result = capture(FakeStreamingSdk(tracker), CaptureOptions("FAKE-001", 0.1), rows.append)

    assert result == "DISCONNECTED"
    sample = next(row for row in rows if row["record_type"] == "gaze")
    assert sample["synthetic"] is False
    assert json.loads(sample["raw_sdk_json"])["device_time_stamp"] == 100
    assert rows[-1]["sample_count"] == 1
    assert rows[-1]["status"] == "DISCONNECTED"
    assert set(tracker.removed) == {"gaze", "lost", "errors"}
    pairs = [row for row in rows if row["record_type"] == "clock_pair"]
    assert [pair["phase"] for pair in pairs] == ["START", "END"]
    assert all("host_perf_counter_before_ns" in pair for pair in pairs)


def test_zero_samples_are_not_reported_as_capture_success() -> None:
    tracker = FakeStreamingTracker()

    def silent(subscription_type: str, callback: SdkCallback,
               as_dictionary: bool = False) -> None:
        tracker.callbacks[subscription_type] = callback

    tracker.subscribe_to = silent
    rows: list[dict] = []

    result = capture(FakeStreamingSdk(tracker), CaptureOptions("FAKE-001", 0), rows.append)

    assert result == "NO_SAMPLES"
    assert rows[-1]["sample_count"] == 0


def test_disconnect_cleanup_error_keeps_partial_samples_and_attempts_all_unsubscribes() -> None:
    tracker = FakeStreamingTracker()
    rows: list[dict] = []

    def fail_unsubscribe(subscription_type: str, callback: SdkCallback) -> None:
        tracker.removed.append(subscription_type)
        if subscription_type == "gaze":
            raise RuntimeError("fake disconnected SDK cleanup")

    tracker.unsubscribe_from = fail_unsubscribe

    with pytest.raises(RuntimeError, match="fake disconnected"):
        capture(FakeStreamingSdk(tracker), CaptureOptions("FAKE-001", 0.1), rows.append)

    assert set(tracker.removed) == {"gaze", "lost", "errors"}
    assert any(row["record_type"] == "gaze" for row in rows)
