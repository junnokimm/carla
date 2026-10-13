from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.p6.clock import AffineClockMapping, SessionOrigin
from src.p6.models import ClockKind, Validity
from src.p6.source import SyntheticGazeSource
from src.p6.tobii.protocol import TobiiSampleDecoder
from src.p6.writer import GazeSidecarWriter
from tools.tobii.sdk import DeviceIdentity, TobiiError


def gaze_record(device_timestamp: int = 100, system_timestamp: int = 200) -> dict:
    raw = {
        "device_time_stamp": device_timestamp,
        "system_time_stamp": system_timestamp,
        "left_gaze_point_on_display_area": [0.25, 0.5],
        "right_gaze_point_on_display_area": [0.75, 0.5],
        "left_gaze_point_validity": 1,
        "right_gaze_point_validity": 1,
        "left_pupil_diameter": 3.25,
        "left_gaze_origin_in_user_coordinate_system": [1.0, 2.0, 3.0],
    }
    return {
        "record_type": "gaze",
        "schema_version": 1,
        "synthetic": False,
        "source_sequence": 0,
        "host_monotonic_ns": 1000,
        "host_wall_time_ns": 2000,
        "calibration_status": "NOT_VERIFIED",
        "device": asdict(DeviceIdentity("Tobii Pro Nano", "FAKE-001", "fake://nano", "fixture", "v0")),
        "raw_sdk_json": json.dumps(raw),
    }


def test_raw_identity_timestamps_and_extra_sdk_fields_survive_writer(tmp_path: Path) -> None:
    record = gaze_record(123456789012345, 234567890123456)
    decoder = TobiiSampleDecoder()
    origin = SessionOrigin(0, 0)
    writer = GazeSidecarWriter(
        path=tmp_path / "gaze.jsonl", run_id="fake-boundary-test", origin=origin,
        mapping=AffineClockMapping.unverified("us", origin), layout=None,
    )
    writer.start()
    try:
        envelope = decoder.decode(json.dumps(record))
        writer.capture(envelope.sample, receive_monotonic_ns=envelope.host_monotonic_ns,
                       receive_utc_ns=envelope.host_wall_time_ns)
    finally:
        writer.close()

    row = json.loads(writer.path.read_text().splitlines()[1])
    assert row["synthetic"] is False
    assert row["source_timestamp"] == 234567890123456
    assert row["clock_kind"] == ClockKind.SYSTEM.value
    assert row["source_metadata"]["device_timestamp_raw"] == 123456789012345
    assert row["source_metadata"]["serial_number"] == "FAKE-001"
    assert row["source_metadata"]["raw_sdk_json"] == record["raw_sdk_json"]
    assert row["source_metadata"]["calibration_status"] == "NOT_VERIFIED"
    assert row["acquisition_session_seconds"] is None


def test_duplicate_and_out_of_order_timestamps_are_retained_and_flagged() -> None:
    decoder = TobiiSampleDecoder()
    records = [gaze_record(100, 200), gaze_record(100, 200), gaze_record(99, 199)]

    samples = [decoder.decode(json.dumps(record)).sample for record in records]

    assert samples[1].source_metadata["duplicate_device_timestamp"] is True
    assert samples[1].source_metadata["duplicate_system_timestamp"] is True
    assert samples[2].source_metadata["backward_device_timestamp"] is True
    assert samples[2].source_metadata["backward_system_timestamp"] is True
    assert samples[2].source_timestamp == 199


def test_synthetic_cannot_cross_real_boundary() -> None:
    record = gaze_record()
    record["synthetic"] = True

    with pytest.raises(TobiiError):
        TobiiSampleDecoder().decode(json.dumps(record))

    assert SyntheticGazeSource().emit(0).source_metadata["synthetic"] is True


@pytest.mark.parametrize("point,validity,reason", [
    ([float("nan"), 0.5], 1, "NONFINITE"),
    ([1.1, 0.5], 1, "OUTSIDE_DISPLAY_AREA"),
    ([0.5, 0.5], 0, "SDK_INVALID"),
])
def test_invalid_gaze_does_not_become_valid_aoi_input(point, validity, reason) -> None:
    record = gaze_record()
    raw = json.loads(record["raw_sdk_json"])
    raw["left_gaze_point_on_display_area"] = point
    raw["left_gaze_point_validity"] = validity
    record["raw_sdk_json"] = json.dumps(raw)

    sample = TobiiSampleDecoder().decode(json.dumps(record)).sample

    assert sample.left.validity is Validity.INVALID
    assert sample.left.invalid_reason == reason
    assert sample.source_metadata["raw_sdk_json"] == record["raw_sdk_json"]
