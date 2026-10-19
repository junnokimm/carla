import io
import json
import time
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from src.p6.external_tablet import ExternalTablet, tracked_eyes
from src.p6.gaze_duration import GazeDuration
from src.p6.tobii.protocol import TobiiSampleDecoder
from tests.test_p6_tobii_capture import FakeStreamingSdk, FakeStreamingTracker
from tests.test_p6_tobii_protocol import gaze_record
from tests.test_pilot_gaze import Probe, Source, collector
from tools.tobii.capture import CaptureOptions, capture


def profile():
    return {"schema_version": 1, "method": "PER_EYE_OBSERVED_CONVEX_HULL",
            "status": "PROVISIONAL_PARTIAL_EMPIRICAL_MAPPING", "serial_number": "FAKE-001",
            "polygons": {eye: [[.8, .9], [1.25, .9], [1.25, 1.4], [.8, 1.4]] for eye in ("left", "right")},
            "display_area": {corner: [1., 2., 3.] for corner in
                             ("top_left", "top_right", "bottom_left", "bottom_right")},
            "missing_targets": ["tablet_top_right"]}


def record(left=(1.1, 1.2), right=(1.1, 1.2), valid=(1, 1), index=0):
    row = gaze_record(100_000 + index * 16_667, 200_000 + index * 16_667)
    raw = json.loads(row["raw_sdk_json"])
    raw.update(left_gaze_point_on_display_area=left, right_gaze_point_on_display_area=right,
               left_gaze_point_validity=valid[0], right_gaze_point_validity=valid[1])
    row.update(raw_sdk_json=json.dumps(raw), source_sequence=index, host_monotonic_ns=time.monotonic_ns(),
               host_perf_counter_ns=time.perf_counter_ns())
    return row


def decoded(**kwargs):
    return TobiiSampleDecoder().decode(json.dumps(record(**kwargs))).sample


def display_record():
    return {"record_type": "display_area", "status": "OK", "serial_number": "FAKE-001",
            "display_area": profile()["display_area"]}


def test_sdk_valid_off_monitor_is_tablet_but_sdk_invalid_is_never_imputed():
    mapping = ExternalTablet(profile())
    item = decoded()
    assert item.left.invalid_reason == "OUTSIDE_DISPLAY_AREA"
    assert len(tracked_eyes(item)) == 2
    assert mapping.classify(item)[:2] == (True, "EMPIRICAL_TABLET_BINOCULAR")
    assert mapping.classify(decoded(valid=(1, 0)))[:2] == (True, "EMPIRICAL_TABLET_MONOCULAR")
    assert not tracked_eyes(decoded(valid=(0, 0)))
    assert not mapping.classify(decoded(valid=(0, 0)))[0]
    assert not tracked_eyes(decoded(left=(float("nan"), 1), right=(1, float("inf"))))


def test_binocular_disagreement_and_foreground_are_not_tablet():
    mapping = ExternalTablet(profile())
    assert mapping.classify(decoded(left=(.5, .4)))[:2] == (False, "TABLET_EYE_DISAGREEMENT")
    for point in ((.5, .4), (.1125, .72), (.8875, .72), (1.4, 1.6)):
        assert not mapping.classify(decoded(left=point, right=point))[0]


def test_display_and_serial_binding_and_malformed_profile():
    mapping = ExternalTablet(profile())
    assert mapping.display_matches(display_record())
    changed = display_record()
    changed["display_area"]["top_left"][0] += 10
    assert not mapping.display_matches(changed)
    assert not mapping.display_matches({"status": "UNAVAILABLE"})
    changed = display_record()
    changed["serial_number"] = "OTHER"
    assert not mapping.display_matches(changed)
    bad = profile()
    bad["polygons"]["left"] = [[0, 0], [1, 1], [2, 2]]
    with pytest.raises(ValueError):
        ExternalTablet(bad)


def test_duration_does_not_bridge_lost_samples_gaps_transitions_or_clock_reset():
    rows = []
    timeline = GazeDuration(0, rows.append)
    timeline.add(10_000_000, 0, 10_000, "TABLET_MONOCULAR")
    timeline.add(30_000_000, 1, 30_000, "TABLET_MONOCULAR")
    timeline.add(50_000_000, 2, 50_000, "TRACKING_LOST")
    timeline.add(70_000_000, 3, 70_000, "TRACKING_LOST")
    timeline.add(1_070_000_000, 4, 1_070_000, "TABLET_MONOCULAR")
    timeline.add(1_090_000_000, 6, 1_090_000, "TABLET_MONOCULAR")
    timeline.add(1_110_000_000, 7, 1, "TABLET_MONOCULAR")
    timeline.add(1_100_000_000, 8, 10, "TABLET_MONOCULAR")  # backward host time ignored
    timeline.finish(1_120_000_000)
    report = timeline.summary()
    assert report["tablet_classified_seconds"] == pytest.approx(.02)
    assert report["tracking_lost_seconds"] == pytest.approx(.02)
    assert report["transition_uncertain_seconds"] == pytest.approx(.02)
    assert report["no_data_seconds"] == pytest.approx(1.06)
    assert report["clock_anomalies"] == 1
    assert report["true_total_tablet_seconds"] is None
    assert sum(row["duration_s"] for row in rows) == pytest.approx(1.12)
    assert sum(report["seconds_by_state"].values()) == pytest.approx(report["observation_seconds"])
    assert all(a["end_clock_ns"] == b["start_clock_ns"] for a, b in pairwise(rows))


def test_zero_samples_and_failure_tail_are_unknown_and_finish_is_idempotent():
    rows = []
    timeline = GazeDuration(100, rows.append)
    timeline.finish(1_000_000_100)
    timeline.finish(1_000_000_100)
    timeline.finish(2_000_000_100)
    assert timeline.summary()["no_data_seconds"] == 2
    assert timeline.summary()["tablet_classified_seconds"] == 0
    assert sum(row["duration_s"] for row in rows) == 2


def setup_collector(tmp_path):
    item = collector(tmp_path, probe_factory=Probe)
    item.config = {"display_confirmed": True}
    item.tablet = ExternalTablet(profile())
    item.wire, item.gaze = io.StringIO(), io.StringIO()
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    return item


@pytest.mark.parametrize("binding,expected", [(True, "TABLET"), (False, "NO_AOI")])
def test_sidecar_only_labels_tablet_after_live_display_binding(tmp_path, binding, expected):
    item = setup_collector(tmp_path)
    if binding:
        item._raw(json.dumps(display_record()))
    item._sample(decoded())
    row = json.loads(item.gaze.getvalue().splitlines()[-1])
    assert row["aoi"] == expected
    assert row["sample"]["left"]["invalid_reason"] == "OUTSIDE_DISPLAY_AREA"
    assert row["observation_state"] == ("TABLET_BINOCULAR" if binding else "VALID_UNMAPPED")
    item._sample(decoded(valid=(0, 0), index=1))
    row = json.loads(item.gaze.getvalue().splitlines()[-1])
    assert row["aoi"] == "NO_AOI"
    assert row["observation_state"] == "TRACKING_LOST"


def test_monitor_mapping_failure_does_not_relabel_as_tablet(tmp_path):
    item = setup_collector(tmp_path)
    item._raw(json.dumps(display_record()))
    item.config["monitor_name"] = "OTHER"
    item._sample(decoded())
    row = json.loads(item.gaze.getvalue().splitlines()[-1])
    assert row["aoi"] == "NO_AOI"
    assert row["observation_state"] == "MAPPING_UNAVAILABLE"


def test_optional_display_read_failure_keeps_sdk_stream_alive():
    tracker = FakeStreamingTracker()  # no get_display_area method
    rows = []
    assert capture(FakeStreamingSdk(tracker), CaptureOptions("FAKE-001", .1, True), rows.append) == "DISCONNECTED"
    assert any(row.get("record_type") == "gaze" for row in rows)
    assert next(row for row in rows if row["record_type"] == "display_area")["status"] == "UNAVAILABLE"


def test_pilot_worker_emits_read_only_display_binding():
    tracker = FakeStreamingTracker()
    tracker.get_display_area = lambda: SimpleNamespace(**profile()["display_area"])
    rows = []
    capture(FakeStreamingSdk(tracker), CaptureOptions("FAKE-001", .1, True), rows.append)
    binding = next(row for row in rows if row["record_type"] == "display_area")
    assert ExternalTablet(profile()).display_matches(binding)
    assert rows.index(binding) < next(i for i, row in enumerate(rows) if row["record_type"] == "gaze")


def test_bad_tablet_profile_keeps_monitor_capture_and_records_failure(tmp_path):
    item = collector(tmp_path, source_factory=Source, probe_factory=Probe)
    item.config_path.write_text(json.dumps({"tobii_python": "unused", "display_confirmed": True,
                                            "tablet_profile": "missing.json"}))
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item.start()
    deadline = time.monotonic() + 3
    while not item.counts["samples"] and time.monotonic() < deadline:
        time.sleep(.01)
    item.close()
    summary = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    assert summary["counts"]["FORWARD"] == 1
    assert summary["tablet_status"] == "TABLET_PROFILE_FAILED"
    assert summary["session_action"] == "CONTINUE"


def test_real_profile_missing_corner_is_explicit_and_not_filled():
    profile_path = Path(__file__).resolve().parents[1] / "config/p8_external_tablet.json"
    item = ExternalTablet.load(profile_path)
    assert item.profile["missing_targets"] == ["tablet_top_right"]
    assert not item.profile["independently_validated"]
    assert not item.profile["target_evidence"]["tablet_bottom_right"]["right"]["included"]


def test_end_to_end_duration_aggregate_and_export(tmp_path):
    class TabletSource(Source):
        def subscribe(self, callback):
            self.record_sink(json.dumps(display_record()))
            coarse_host_clock = time.monotonic_ns()
            for index in range(4):
                row = record(index=index, valid=(1, 0))
                # Python 3.10's coarse clock can repeat; high-resolution duration must survive.
                row["host_monotonic_ns"] = coarse_host_clock
                self.record_sink(json.dumps(row))
                callback(TobiiSampleDecoder().decode(json.dumps(row)).sample)
                time.sleep(.017)
            self.last_error = "DISCONNECTED"
            self.finished.set()

    item = collector(tmp_path, source_factory=TabletSource, probe_factory=Probe)
    (tmp_path / "tablet.json").write_text(json.dumps(profile()))
    item.config_path.write_text(json.dumps({"tobii_python": "unused", "display_confirmed": True,
                                            "tablet_profile": "tablet.json"}))
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item.start()
    item.thread.join(3)
    assert not item.thread.is_alive()
    time.sleep(.03)  # collector already stopped but experiment remains active
    item.close()
    summary = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    totals = json.loads((item.directory / "gaze_totals.json").read_text())
    assert summary["status"] == "FAILED"
    assert summary["observation_window_closed"]
    assert summary["counts"]["TABLET"] == 4
    assert summary["duration"]["no_data_seconds"] >= .03
    assert 0 < totals["tablet_classified_seconds"] < .3
    assert totals["seconds_by_state"]["TABLET_MONOCULAR"] == totals["tablet_classified_seconds"]
    intervals = [json.loads(line) for line in next((item.directory / "gaze").glob("*/intervals.jsonl")).read_text().splitlines()]
    assert sum(row["duration_s"] for row in intervals) == pytest.approx(summary["duration"]["observation_seconds"])
    from src.p8.operator_results import export_operator_results
    (item.directory / "session_manifest.json").write_text(json.dumps({"state": "ABORTED", "run_id": item.directory.name}))
    exported = export_operator_results(item.directory, closed=True)
    assert exported.error is None
    with ZipFile(exported.archive) as archive:
        names = archive.namelist()
        assert any(name.endswith("/intervals.jsonl") for name in names)
        assert any(name.endswith("/gaze_totals.json") for name in names)
        assert not any(".gaze_pending" in name for name in names)
