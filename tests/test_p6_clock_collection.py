import json

from src.p6.clock import SessionOrigin
from src.p6.clock_evidence import read_clock_evidence
from tests.test_p6_tobii_capture import FakeStreamingSdk, FakeStreamingTracker
from tools.tobii.capture import CaptureOptions, capture


def test_start_middle_end_pairs_span_capture_without_startup_burst(monkeypatch):
    tracker = FakeStreamingTracker()
    tracker.subscribe_to = lambda topic, callback, as_dictionary=False: tracker.callbacks.__setitem__(topic, callback)
    sdk = FakeStreamingSdk(tracker)
    now = [0.0]

    def advancing():
        now[0] += 0.5
        return now[0]

    monkeypatch.setattr("tools.tobii.capture.time.monotonic", advancing)
    rows = []

    result = capture(sdk, CaptureOptions("FAKE-001", 10), rows.append)

    assert result == "NO_SAMPLES"
    pairs = [row for row in rows if row["record_type"] == "clock_pair"]
    assert pairs[0]["phase"] == "START"
    assert pairs[-1]["phase"] == "END"
    assert any(pair["phase"] == "MIDDLE" for pair in pairs)
    assert len(pairs) >= 5
    assert all("host_before_ns" in pair and "host_perf_counter_before_ns" in pair for pair in pairs)


def test_evidence_coverage_and_failed_pair_cannot_be_hidden(tmp_path):
    path = tmp_path / "clock.jsonl"
    rows = [{"record_type": "clock_pair", "phase": phase, "sdk_system_us": i * 1_000_000,
             "host_before_ns": i * 1_000_000_000, "host_after_ns": i * 1_000_000_000}
            for i, phase in enumerate(("START", "MIDDLE", "MIDDLE", "MIDDLE", "END"))]
    rows += [{"record_type": "gaze", "raw_sdk_json": json.dumps({"system_time_stamp": 2_000_000})},
             {"record_type": "gaze", "raw_sdk_json": json.dumps({"system_time_stamp": 5_000_000})},
             {"record_type": "clock_pair_error", "phase": "END"}]
    path.write_text("\n".join(json.dumps(row) for row in rows))

    evidence = read_clock_evidence(path)

    assert evidence.report()["gaze_samples_inside_pair_range"] == 1
    assert evidence.report()["gaze_samples_outside_pair_range"] == 1
    assert evidence.fit(SessionOrigin(0, 0)).provenance.reason == "CLOCK_RECORD_ERROR"
    assert not evidence.fit(SessionOrigin(0, 0)).verified
