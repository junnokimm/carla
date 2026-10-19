import csv
import json
from pathlib import Path

import pytest

from src.p8.collection_clock import fit_clock, map_time, map_tobii, tobii_alignment
from src.p8.collection_gaze import gaze_metrics
from src.p8.collection_hazard import hazard_metrics
from src.p8.collection_io import save_json
from src.p8.collection_report import (
    active_windows,
    build_reports,
    exclude_pauses,
    finalize_collection,
)
from src.p8.collection_surt import external_surt_reference


def identity_alignment():
    fit = fit_clock([(x, x, 0) for x in range(11)])
    return {"sdk_to_shared_qpc": fit, "shared_qpc_to_session": fit}


def test_clock_origins_drift_and_no_extrapolation():
    pairs = [(100000 + x, 15 + 1.0001 * x, 0.0001) for x in range(20)]
    fit = fit_clock(pairs)
    assert fit["status"] == "SOFTWARE_ALIGNMENT_CHECKED"
    assert fit["drift_ppm"] == pytest.approx(100)
    assert map_time(fit, 100010) == pytest.approx(25.001)
    assert map_time(fit, 100020) is None
    assert fit["physical_sync_validated"] is False


@pytest.mark.parametrize(
    "pairs,status",
    [
        ([(i, i, 0) for i in range(4)], "INSUFFICIENT_EVIDENCE"),
        ([(i, i, 0.03) for i in range(5)], "QUALITY_CHECK_FAILED"),
        (
            [(0, 0, 0), (1, 1, 0), (2, 2, 0), (1, 3, 0), (4, 4, 0)],
            "CLOCK_RESET_OR_NONMONOTONIC",
        ),
        ([(i, i + (i % 2) * 0.2, 0) for i in range(5)], "QUALITY_CHECK_FAILED"),
    ],
)
def test_clock_rejects_inadequate_evidence(pairs, status):
    fit = fit_clock(pairs)
    assert fit["status"] == status
    assert map_time(fit, 2) is None


def test_tobii_bridges_different_worker_monotonic_origin():
    records = []
    for i in range(10):
        records += [
            {
                "record_type": "clock_pair",
                "sdk_system_us": (100 + i) * 1e6,
                "host_before_ns": (500 + i) * 1e9,
                "host_after_ns": (500 + i) * 1e9,
                "host_perf_counter_before_ns": (1000 + i) * 1e9,
                "host_perf_counter_after_ns": (1000 + i) * 1e9,
            },
            {
                "record_type": "parent_clock_bridge",
                "host_before_ns": (1100 + i) * 1e9,
                "host_after_ns": (1100 + i) * 1e9,
                "perf_counter_ns": (1000 + i) * 1e9,
            },
        ]
    alignment = tobii_alignment(records, 1100_000_000_000)
    assert alignment["status"] == "SOFTWARE_ALIGNMENT_CHECKED"
    assert map_tobii(alignment, 105_000_000) == pytest.approx(5)


def gaze(t, seq, aoi="FORWARD", tracked=True, state=None):
    return {
        "record_type": "gaze",
        "sample": {
            "source_sequence": seq,
            "source_metadata": {"system_timestamp_raw": t * 1e6},
        },
        "sdk_tracked_eyes": ["left"] if tracked else [],
        "aoi": aoi,
        "observation_state": state or aoi,
    }


def test_gaze_offscreen_tablet_denominator_phase_exclusions_and_failed_aoi():
    samples = [
        gaze(0.5, 0),
        gaze(1, 1),
        gaze(1.02, 2, "TABLET", state="TABLET_MONOCULAR"),
        gaze(1.04, 3, "NO_AOI", False, "TRACKING_LOST"),
        gaze(1.06, 4, "NO_AOI", True, "AOI_UNCERTAIN"),
        gaze(3, 5),
    ]
    report, _ = gaze_metrics(
        samples,
        windows=[(1, 2)],
        alignment=identity_alignment(),
        eligible={"FORWARD": True, "TABLET": False},
        hazard_start=1.05,
    )
    all_m2 = report["cohorts"]["all_active_m2"]
    assert all_m2["counts"]["received_samples"] == 4
    assert all_m2["raw_sample_ratio_by_aoi"]["TABLET"] == pytest.approx(1 / 3)
    assert all_m2["analysis_sample_ratio_by_aoi"]["TABLET"] is None
    assert all_m2["analysis_sample_ratio_by_aoi"]["FORWARD"] == pytest.approx(1 / 3)
    assert all_m2["tracking_loss_sample_ratio"] == 0.25
    assert report["cohorts"]["before_hazard"]["counts"]["received_samples"] == 3


def test_gaze_no_samples_or_unaligned_not_zero_and_gap_not_imputed():
    samples = [gaze(1, 1, "TABLET"), gaze(1.02, 2, "TABLET"), gaze(1.9, 50, "TABLET")]
    report, _ = gaze_metrics(
        samples,
        windows=[(0, 3)],
        alignment=identity_alignment(),
        eligible={"TABLET": True},
    )
    cohort = report["cohorts"]["all_active_m2"]
    assert cohort["analysis_observed_seconds_by_aoi"]["TABLET"] == pytest.approx(0.02)
    assert cohort["unobserved_or_gap_seconds"] == pytest.approx(2.98)
    report, _ = gaze_metrics(
        [], windows=[(0, 3)], alignment=identity_alignment(), eligible={"TABLET": True}
    )
    assert (
        report["cohorts"]["all_active_m2"]["analysis_sample_ratio_by_aoi"]["TABLET"]
        is None
    )


def test_gaze_callback_fallback_is_explicit_and_unmapped_legacy_is_excluded():
    bad_fit = {
        "sdk_to_shared_qpc": fit_clock([]),
        "shared_qpc_to_session": fit_clock([]),
    }
    a, b = gaze(1, 1), gaze(1.02, 2)
    a["parent_callback_session_s"] = 1.001
    report, aligned = gaze_metrics(
        [a, b], windows=[(0, 3)], alignment=bad_fit, eligible={"FORWARD": True}
    )
    assert aligned[0]["time_basis"] == "CALLBACK_BRIDGE_APPROXIMATE"
    assert aligned[1]["session_elapsed_s"] is None
    assert report["cohorts"]["all_active_m2"]["counts"]["received_samples"] == 1


def test_recovery_pause_and_collision_wait_are_not_active_time():
    manifest = {
        "origin": {"monotonic_ns": 100_000_000_000},
        "components": {
            "runtime_faults": [
                {"host_started_s": 102, "paused_seconds": 3},
                {"host_started_s": 108},
            ]
        },
    }
    assert exclude_pauses([(0, 10)], manifest) == [(0, 2), (5, 8)]


def test_only_literal_runtime_boundaries_define_driving_windows():
    events = [
        {"phase": "MODULE_2", "event_type": n, "event_value": v, "session_elapsed_s": t}
        for n, v, t in (
            ("stage_start", '{"stage":"MODULE_2_PREPARE"}', 0),
            ("stage_end", '{"stage":"MODULE_2_PREPARE"}', 20),
            ("stage_start", '{"stage":"MODULE_2_RUNNING"}', 20),
            ("stage_start", "MODULE_2_RUNNING", 23),
            ("stage_end", "MODULE_2_RUNNING:HAZARD_COLLISION", 600),
            ("stage_end", '{"stage":"MODULE_2_RUNNING"}', 650),
        )
    ]
    assert active_windows(events, "MODULE_2", 600) == [(23, 600)]


def hazard_events(*, response="BRAKE", gap=False, baseline_brake=0):
    samples = []
    for step in range(-2, 102):
        t = step * 0.05
        if gap and 0.3 < t < 0.8:
            continue
        samples.append(
            {
                "poll_started_monotonic_ns": int((10 + t) * 1e9) - 100,
                "poll_completed_monotonic_ns": int((10 + t) * 1e9),
                "input": {
                    "brake": 0.2 if t >= 1 and response == "BRAKE" else baseline_brake,
                    "steering": 0.2 if t >= 1 and response == "STEER" else 0,
                },
                "geometry": {"ttc_candidate_s": 3},
            }
        )
    return [
        (
            "m2_hazard_display_candidate",
            10,
            {
                "flip_completed_monotonic_ns": 10_000_000_000,
                "normal_window_candidate": True,
                "geometry": {"ego_speed_mps": 20},
            },
        ),
        ("m2_hazard_raw_samples", 11, {"batch_id": "B1", "samples": samples}),
    ]


@pytest.mark.parametrize("kind", ["BRAKE", "STEER"])
def test_hazard_threshold_response_and_duplicate_batch(kind):
    events = hazard_events(response=kind)
    report = hazard_metrics(events + events[-1:], 0)
    assert report["status"] == "RESPONSE_OBSERVED"
    assert report["first_response_type"] == kind
    assert report["reaction_time_s"] == pytest.approx(1)
    assert report["ttc_candidate_at_response_s"] == 3
    assert report["outcome"] == "CENSORED"


def test_hazard_missing_vs_no_response_and_preheld_brake():
    assert (
        hazard_metrics(hazard_events(response=None), 0)["status"] == "NO_RESPONSE_IN_5S"
    )
    assert (
        hazard_metrics(hazard_events(response=None, gap=True), 0)["status"]
        == "INSUFFICIENT_DATA"
    )
    assert (
        hazard_metrics(hazard_events(response=None, baseline_brake=0.2), 0)["status"]
        == "PREEXISTING_BRAKE_NO_NEW_RESPONSE"
    )
    gapped = hazard_metrics(hazard_events(gap=True), 0)
    assert gapped["reaction_time_s"] == 1
    assert gapped["analysis_reaction_time_s"] is None
    assert hazard_metrics([], 0)["reaction_time_s"] is None


def test_hazard_collision_and_pass_are_not_invented():
    events = hazard_events()
    events += [("m2_hazard_cargo_passed", 15, {})]
    assert hazard_metrics(events, 0)["outcome"] == "PASSED_WITHOUT_RECORDED_COLLISION"
    events += [
        ("m2_hazard_end", 16, {"collision_stop": {"reason": "HAZARD_COLLISION"}})
    ]
    assert hazard_metrics(events, 0)["outcome"] == "COLLISION"
    no_display = hazard_metrics(
        [("m2_hazard_collision_stop_wait", 10, {"reason": "HAZARD_COLLISION"})], 0
    )
    assert no_display["outcome"] == "COLLISION"
    assert no_display["reaction_time_s"] is None


def test_tracking_loss_does_not_span_an_excluded_pause():
    samples = [
        gaze(t, i, "NO_AOI", False, "TRACKING_LOST")
        for i, t in enumerate((1, 1.02, 2, 3, 3.02))
    ]
    report, _ = gaze_metrics(
        samples, windows=[(1, 1.5), (3, 4)], alignment=identity_alignment(), eligible={}
    )
    assert report["cohorts"]["all_active_m2"][
        "longest_contiguous_tracking_loss_s"
    ] == pytest.approx(0.02)


@pytest.mark.parametrize(
    "condition,status",
    [
        ("SURT", "EXTERNAL_APP_COLLECTS_SEPARATELY"),
        ("NO_SURT", "NOT_APPLICABLE_NO_SURT"),
    ],
)
def test_surt_reference_does_not_claim_to_read_external_app(condition, status):
    report = external_surt_reference(
        {
            "run_id": "R",
            "participant_id": "P1",
            "origin": {"monotonic_ns": 10},
            "study_context": {"module_1_condition": condition},
        },
        [(30, 630)],
    )
    assert report["status"] == status
    assert report["trial_count"] is None
    assert report["app_logs_inspected"] is False
    assert report["m1_active_windows_session_s"] == [(30, 630)]


def test_report_io_failure_cannot_raise_to_experiment(tmp_path, monkeypatch):
    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", disk_full)
    finalize_collection(tmp_path)


def test_manifest_io_failure_keeps_session_alive_and_records_error(
    tmp_path, monkeypatch
):
    from tests.test_p8_operator_backend import StrictClock, register_dev

    controller, _ = register_dev(tmp_path, StrictClock())
    services = controller.session.services
    original = services._save_manifest_disk

    def fail(payload):
        raise OSError("manifest disk full")

    monkeypatch.setattr(services, "_save_manifest_disk", fail)
    services.save_manifest({"state": "RUNNING"})
    assert controller.session.closed is False
    assert services.logger.collection_failure_count == 1
    monkeypatch.setattr(services, "_save_manifest_disk", original)
    controller.abort("TEST_END")
    saved = json.loads((services.directory / "session_manifest.json").read_text())
    assert saved["logging_health"]["failure_count"] == 1


def test_end_to_end_export_reports_preserve_original_logs(tmp_path):
    run = tmp_path / "R"
    run.mkdir()
    save_json(
        run / "session_manifest.json",
        {
            "run_id": "R",
            "participant_id": "P1",
            "origin": {"monotonic_ns": 0},
            "study_context": {"module_1_condition": "NO_SURT"},
            "state": "ABORTED",
        },
    )
    event_file = run / "research_events_R.csv"
    event_file.write_text(
        "phase,event_type,event_value,session_elapsed_s\nMODULE_1,initial_state,AVAILABLE|MANUAL,0\nMODULE_1,stage_start,MODULE_1_RUNNING,0\nMODULE_1,auto_state,AVAILABLE|NOA_ACTIVE,3\nMODULE_1,stage_end,MODULE_1_RUNNING:NORMAL_COMPLETION,6\n"
    )
    (run / "research_telemetry_R.csv").write_text(
        "phase,session_elapsed_s,carla_frame,carla_simulation_seconds\n"
        + "".join(f"MODULE_1,{i},{i + 1},{i}\n" for i in range(7))
    )
    original = event_file.read_bytes()
    build_reports(run)
    assert event_file.read_bytes() == original
    with (run / "collection/MODULE_1_vehicle.csv").open() as stream:
        assert [r["noa_state"] for r in csv.DictReader(stream)] == ["0"] * 3 + ["1"] * 4
    clock = json.loads((run / "collection/clock_sync.json").read_text())
    assert len(clock["markers"]) == 5
    assert clock["three_clock_software_check"] is False
    assert (
        json.loads((run / "collection/surt_external_link.json").read_text())["status"]
        == "NOT_APPLICABLE_NO_SURT"
    )


def test_disk_write_failure_keeps_driver_inputs_and_module_progress(tmp_path):
    from src.experiment.automation_interaction import DriverInput
    from src.p8.model import Stage
    from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev

    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    expected = [DriverInput(throttle=0.4, brake=0.3, steering=0.1)]
    evidence.driver_inputs[:] = expected
    logger = controller.session.services.logger

    class BrokenWriter:
        def writerow(self, *args):
            raise OSError("disk full injected")

    logger._event_writer = BrokenWriter()
    logger._telemetry_writer = BrokenWriter()
    result = controller.execute_current_module(5)
    assert result.actual_active_drive_s > 0
    assert logger.collection_failure_count > 0
    assert evidence.observed_driver_inputs == expected
    controller.abort("TEST_END")
