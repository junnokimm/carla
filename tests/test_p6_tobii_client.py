from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from src.p6.models import SourceStatus
from src.p6.source import GazeSource, RealSourceUnavailableError
from src.p6.tobii.client import TobiiProcessSource
from tests.test_p6_tobii_protocol import gaze_record
from tools.tobii.sdk import DeviceIdentity


def configured_source(monkeypatch, script: str) -> TobiiProcessSource:
    source = TobiiProcessSource(Path(sys.executable))
    identity = asdict(DeviceIdentity("Tobii Pro Nano", "FAKE-001", "fake://nano", "fixture", "v0"))
    discovery = json.dumps({"status": "OK", "devices": [identity]})
    monkeypatch.setattr(source, "_command", lambda action: [
        sys.executable, "-B", "-c", f"print({discovery!r})" if action == "discover" else script
    ])
    source.discover()
    source.select("FAKE-001")
    return source


def test_disconnect_is_contained_and_does_not_block_caller(monkeypatch) -> None:
    wire = json.dumps(gaze_record())
    script = f"print({wire!r}, flush=True); print('{{\"record_type\":\"status\",\"status\":\"DISCONNECTED\"}}', flush=True)"
    source = configured_source(monkeypatch, script)
    received = []
    contract: GazeSource = source

    started = time.monotonic()
    contract.subscribe(received.append)
    caller_can_continue = time.monotonic() - started < 0.5
    assert source.finished.wait(3)
    contract.unsubscribe()

    assert caller_can_continue
    assert source.status is SourceStatus.DISCONNECTED
    assert source.last_error == "DISCONNECTED"
    assert len(received) == 1
    assert received[0].source_metadata["synthetic"] is False


def test_unresponsive_worker_can_be_stopped_with_bounded_wait(monkeypatch) -> None:
    source = configured_source(monkeypatch, "import time; time.sleep(60)")
    source.subscribe(lambda sample: None)

    start = time.monotonic()
    source.unsubscribe()

    assert time.monotonic() - start < 1.5
    assert source.finished.wait(2)
    assert source.status is SourceStatus.DISCONNECTED


def test_sdk_absence_never_falls_back_to_synthetic(monkeypatch) -> None:
    source = TobiiProcessSource(Path(sys.executable))
    monkeypatch.setattr(source, "_command", lambda action: [
        sys.executable, "-c", "print('{\"status\":\"SDK_NOT_INSTALLED\",\"devices\":[]}')"
    ])

    with pytest.raises(RealSourceUnavailableError):
        source.discover()

    assert source.status is SourceStatus.SDK_ABSENT
    assert source.last_error == "SDK_NOT_INSTALLED"


def test_adapter_does_not_claim_external_calibration_success(monkeypatch) -> None:
    source = configured_source(monkeypatch, "pass")

    with pytest.raises(RealSourceUnavailableError):
        source.calibrate()

    assert source.calibration_state == "NOT_VERIFIED"


def test_malformed_worker_output_stops_process_without_waiting_for_watchdog(monkeypatch) -> None:
    source = configured_source(monkeypatch, "import time; print('not-json', flush=True); time.sleep(60)")

    source.subscribe(lambda sample: None)
    try:
        assert source.finished.wait(2)
    finally:
        source.unsubscribe()

    assert source.status is SourceStatus.DISCONNECTED
    assert source.last_error is not None
