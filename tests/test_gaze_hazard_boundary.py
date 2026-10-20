import csv
import json

import pytest

from src.p8.collection_gaze import gaze_metrics
from src.p8.collection_hazard_boundary import hazard_boundary
from src.p8.collection_report import build_reports
from tests.test_p8_collection import gaze, identity_alignment


def event(name, t=None, **payload):
    return {"event_type": "m2_hazard_" + name, "session_elapsed_s": "9.0",
            "analysis_elapsed_s": t, "payload": payload}


def clear_events(t=2.0):
    return [event("scheduled", exposure_policy="CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD"),
            event("obstacle_reveal_requested", 1.8),
            event("display_candidate", t, flip_completed_monotonic_ns=2_000_000_000)]


def test_new_scene_uses_actual_display_and_legacy_keeps_preparation_boundary():
    boundary = hazard_boundary(clear_events(), qpc=True)
    assert boundary["start_s"] == 2 and boundary["source_event"] == "m2_hazard_display_candidate"
    assert hazard_boundary(clear_events(), qpc=False, origin_ns=0)["start_s"] == 2
    legacy = [event("preparation_started", 1), event("display_candidate", 2)]
    assert hazard_boundary(legacy, qpc=True)["start_s"] == 1
    assert hazard_boundary(legacy, qpc=False)["start_s"] == 9


@pytest.mark.parametrize("rows,status", [
    (clear_events(None), "BOUNDARY_CLOCK_UNAVAILABLE"),
    (clear_events()[:2], "BOUNDARY_UNAVAILABLE"),
    ([], "BOUNDARY_UNAVAILABLE"),
])
def test_unknown_boundary_never_labels_the_whole_drive_as_before_hazard(rows, status):
    boundary = hazard_boundary(rows, qpc=True)
    report, _ = gaze_metrics([gaze(1, 1), gaze(2, 2)], windows=[(0, 3)],
                             alignment=identity_alignment(), eligible={"FORWARD": True},
                             hazard_boundary=boundary, hazard_start=boundary["start_s"])
    before = report["cohorts"]["before_hazard"]
    assert before["status"] == status
    assert before["active_window_s"] is None
    assert before["analysis_sample_ratio_by_aoi"]["FORWARD"] is None
    assert report["cohorts"]["all_active_m2"]["counts"]["received_samples"] == 2


def test_confirmed_nonoccurrence_includes_whole_drive_and_does_not_hide_reveal():
    rows = [clear_events()[0], event("not_occurred")]
    boundary = hazard_boundary(rows, qpc=True)
    assert boundary["available"] and boundary["status"] == "CONFIRMED_NOT_OCCURRED"
    assert not hazard_boundary(rows + [event("obstacle_reveal_requested")], qpc=True)["available"]


def test_zero_time_boundary_excludes_all_samples():
    boundary = hazard_boundary(clear_events(0.0), qpc=True)
    report, _ = gaze_metrics([gaze(0, 1), gaze(.02, 2)], windows=[(0, 3)],
                             alignment=identity_alignment(), eligible={"FORWARD": True},
                             hazard_boundary=boundary, hazard_start=boundary["start_s"])
    assert report["cohorts"]["before_hazard"]["active_window_s"] == 0
    assert report["cohorts"]["before_hazard"]["status"] == "NO_USABLE_SAMPLES"


def test_report_connects_new_scene_log_to_gaze_without_touching_originals(tmp_path, monkeypatch):
    from src.p8 import collection_report
    (tmp_path / "session_manifest.json").write_text(json.dumps({
        "run_id": "R", "participant_id": "P", "origin": {"monotonic_ns": 0},
        "study_context": {"module_1_condition": "NO_SURT"}}))
    with (tmp_path / "research_events_R.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["phase", "event_type", "event_value", "session_elapsed_s"])
        writer.writerow(["MODULE_2", "stage_start", "MODULE_2_RUNNING", 0])
        for row in clear_events():
            writer.writerow(["MODULE_2", row["event_type"], json.dumps(row["payload"]), row["session_elapsed_s"]])
        writer.writerow(["MODULE_2", "stage_end", "MODULE_2_RUNNING:NORMAL_COMPLETION", 10])
    (tmp_path / "research_telemetry_R.csv").write_text("phase,session_elapsed_s,carla_frame,carla_simulation_seconds\n")
    folder = tmp_path / "gaze" / "MODULE_2_RUNNING-test"
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"stage": "MODULE_2_RUNNING", "status": "COLLECTED",
        "gaze_precheck": {"eligible_aois": {"FORWARD": True}}}))
    (folder / "raw_gaze.jsonl").write_text("")
    (folder / "gaze.jsonl").write_text("\n".join(json.dumps(gaze(t, i + 1)) for i, t in enumerate([1, 1.02, 2, 2.02])))
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(collection_report, "tobii_alignment", lambda *args, **kwargs: identity_alignment() | {"status": "SOFTWARE_ALIGNMENT_CHECKED"})
    build_reports(tmp_path)
    report = json.loads((tmp_path / "collection" / "m2_gaze_metrics.json").read_text())["captures"][0]
    assert report["hazard_exclusion_start_s"] == 2.0  # Not the log write time, 9.0.
    assert report["cohorts"]["before_hazard"]["counts"]["received_samples"] == 2
    assert report["cohorts"]["all_active_m2"]["counts"]["received_samples"] == 4
    assert all(p.read_bytes() == original for p, original in originals.items())
