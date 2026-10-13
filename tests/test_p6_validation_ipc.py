import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from src.p6.tobii.client import TobiiProcessSource
from tests.test_p6_tobii_capture import FakeStreamingSdk, FakeStreamingTracker
from tests.test_p6_tobii_client import configured_source
from tests.test_p6_tobii_protocol import gaze_record
from tools.tobii.capture import CaptureOptions, capture
from tools.tobii.sdk import TobiiError


def test_longer_capture_requires_explicit_bounded_validation_mode() -> None:
    with pytest.raises(TobiiError):
        TobiiProcessSource(Path(sys.executable), seconds=120)
    with pytest.raises(TobiiError):
        TobiiProcessSource(Path(sys.executable), seconds=181, validation=True)

    source = TobiiProcessSource(Path(sys.executable), seconds=120, validation=True)

    assert "validate" in source._command("gaze")


def test_ipc_sink_preserves_identity_clock_and_status_lines(monkeypatch) -> None:
    lines = ['{"record_type":"identity","device":{}}', json.dumps(gaze_record()),
             '{"record_type":"status","status":"DISCONNECTED"}']
    source = configured_source(monkeypatch, ";".join(f"print({line!r}, flush=True)" for line in lines))
    saved = []
    source.record_sink = saved.append

    source.subscribe(lambda sample: None)
    assert source.finished.wait(3)
    source.unsubscribe()

    assert [json.loads(line)["record_type"] for line in saved] == ["identity", "gaze", "status"]


def test_validation_stop_request_drains_without_claiming_calibration() -> None:
    stopped = threading.Event()
    tracker = FakeStreamingTracker()

    def subscribe(topic, callback, as_dictionary=False):
        tracker.callbacks[topic] = callback
        if topic == "gaze":
            callback(json.loads(gaze_record()["raw_sdk_json"]))
            stopped.set()

    tracker.subscribe_to = subscribe
    records = []

    result = capture(FakeStreamingSdk(tracker), CaptureOptions("FAKE-001", 120),
                     records.append, stop_requested=stopped)

    assert result == "RAW_CAPTURED"
    assert records[-1]["sample_count"] == 1
    assert records[-1]["calibration_status"] == "NOT_VERIFIED"


def test_validation_worker_can_exit_with_parent_stdin_still_open() -> None:
    script = """
from tools.tobii import worker
worker.load_sdk = lambda: None
def fake_capture(sdk, options, emit, *, stop_requested):
    stop_requested.wait(0.05)
    return 'RAW_CAPTURED'
worker.capture = fake_capture
raise SystemExit(worker.main(['validate', '--serial', 'FAKE_ONLY', '--seconds', '20']))
"""
    with subprocess.Popen([sys.executable, "-B", "-c", script], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            code = process.wait(timeout=4)
            _, errors = process.communicate(timeout=1)
        finally:
            if process.poll() is None:
                process.kill()

    assert code == 0, errors.decode(errors="replace")
