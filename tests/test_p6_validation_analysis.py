import json
from dataclasses import replace

from src.p6.aoi import Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.clock import SessionOrigin
from src.p6.validation.analysis import analyze
from src.p6.validation.recording import RawRecorder
from src.p6.validation.segments import SegmentSequence
from tests.test_p6_tobii_protocol import gaze_record
from tests.test_p6_validation_mapping import geometry


def test_partial_capture_preserves_markers_raw_data_and_descriptive_counts(tmp_path) -> None:
    sequence = SegmentSequence("fake-validation", 0)
    sequence.prepare(0)
    sequence.start(1_000_000_000)
    sequence.abort(2_000_000_000, "ESC")
    recorder = RawRecorder(tmp_path / "raw_gaze.jsonl", SessionOrigin(0, 0))
    for timestamp in (500_000_000, 1_500_000_000, 2_500_000_000):
        record = gaze_record()
        record["host_monotonic_ns"] = timestamp
        recorder.capture(json.dumps(record))
    recorder.close()
    observed = replace(geometry(), monitor=Rect(0, 0, 1280, 720),
                       client=Rect(0, 0, 1280, 720), dpi=96)

    report = analyze(tmp_path, load_aoi_layout("config/p6_aoi_candidate.json"),
                     sequence, geometry=observed, operator_confirmed=True)

    rows = [json.loads(line) for line in (tmp_path / "gaze_with_candidate_aoi.jsonl").read_text().splitlines()]
    assert [row["expected_target"] for row in rows] == [None, "FORWARD", None]
    assert rows[1]["segment_index"] == 0
    assert rows[1]["segment_start_session_s"] == 1
    assert rows[1]["segment_elapsed_s"] == 0.5
    assert json.loads(rows[1]["raw_sdk_json"])["device_time_stamp"] == 100
    assert report["targets"]["FORWARD"]["total_samples"] == 1
    assert report["targets"]["FORWARD"]["expected_aoi_hit_ratio"] == 1
    assert report["targets"]["REAR_MIRROR"]["expected_aoi_hit_ratio"] is None
    assert report["status"] == "ABORTED"
    assert report["aoi_validated"] is False
    assert report["tablet_status"] == "NOT_TESTED"
    assert "pass" not in report
    assert rows[1]["production_aoi"] == "INVALID_LAYOUT"


def test_missing_segment_counts_invalid_eyes_without_guessing_blink(tmp_path) -> None:
    sequence = SegmentSequence("fake-validation", 0)
    sequence.index = 4
    sequence.prepare(0)
    sequence.start(0)
    sequence.advance(2_000_000_000)
    record = gaze_record()
    raw = json.loads(record["raw_sdk_json"])
    raw["left_gaze_point_validity"] = raw["right_gaze_point_validity"] = 0
    record["raw_sdk_json"] = json.dumps(raw)
    recorder = RawRecorder(tmp_path / "raw_gaze.jsonl", SessionOrigin(0, 0))
    recorder.capture(json.dumps(record))
    recorder.close()

    report = analyze(tmp_path, load_aoi_layout("config/p6_aoi_candidate.json"),
                     sequence, geometry=geometry(), operator_confirmed=True)

    missing = report["targets"]["MISSING"]
    assert missing["any_eye_valid_ratio"] == 0
    assert missing["both_eye_valid_ratio"] == 0
    assert missing["invalid_missing_count"] == 1
    assert missing["expected_aoi_hit_count"] == 1
    assert missing["confusion_counts"] == {"INVALID": 1}
    assert missing["median_normalized_x"] is None


def test_unconfirmed_display_does_not_produce_candidate_hits(tmp_path) -> None:
    sequence = SegmentSequence("fake-validation", 0)
    sequence.start(0)
    sequence.abort(2_000_000_000, "UNCONFIRMED")
    recorder = RawRecorder(tmp_path / "raw_gaze.jsonl", SessionOrigin(0, 0))
    recorder.capture(json.dumps(gaze_record()))
    recorder.close()

    report = analyze(tmp_path, load_aoi_layout("config/p6_aoi_candidate.json"),
                     sequence, geometry=geometry(), operator_confirmed=False)

    assert report["targets"]["FORWARD"]["classifiable_sample_count"] == 0
