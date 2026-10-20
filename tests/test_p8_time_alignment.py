import csv
import json
from types import SimpleNamespace

import pytest

from src.experiment import observation_clock
from src.p8.collection_clock import map_tobii, tobii_alignment, validated_clock_fit
from src.p8.collection_gaze import gaze_metrics
from src.p8.collection_hazard import hazard_metrics
from src.p8.collection_report import build_reports
from src.p8.collection_time_axis import qpc_windows
from tests.test_p8_collection import gaze, hazard_events


def clock_records():
    # Independent, grossly different origins, and a coarse parent clock that
    # intentionally fails the old 10 ms fit. The shared QPC is unaffected.
    rows = []
    for i in range(21):
        rows.extend(
            [
                {
                    "record_type": "clock_pair",
                    "sdk_system_us": (100 + i) * 1e6,
                    "host_perf_counter_before_ns": (1000 + i) * 1e9 - 100_000,
                    "host_perf_counter_after_ns": (1000 + i) * 1e9 + 100_000,
                },
                {
                    "record_type": "parent_clock_bridge",
                    "perf_counter_ns": (1000 + i) * 1e9,
                    "host_before_ns": (2000 + i + (i % 2) * 0.04) * 1e9,
                    "host_after_ns": (2000 + i + (i % 2) * 0.04) * 1e9,
                },
            ]
        )
    return rows


def new_alignment():
    return tobii_alignment(
        clock_records(), 2000_000_000_000, origin_perf_counter_ns=1000_000_000_000
    )


def test_acquisition_ignores_callback_latency_and_coarse_control_clock():
    alignment = new_alignment()
    assert alignment["status"] == "SOFTWARE_ALIGNMENT_CHECKED"
    assert alignment["shared_qpc_to_session"]["status"] == "QUALITY_CHECK_FAILED"
    fit = alignment["sdk_to_shared_qpc"]
    assert fit["holdout_count"] == 5 and fit["training_pair_count"] == 16
    assert [p["pair_index"] for p in fit["holdout_checks"]] == [1, 5, 10, 15, 19]
    assert map_tobii(alignment, 105_000_000) == pytest.approx(5)
    assert map_tobii(alignment, 121_000_000) is None
    row = gaze(105, 1, "TABLET")
    row["parent_callback_session_s"] = 800  # Wrong domain / delayed arrival.
    row["sample"]["source_metadata"]["sdk_callback_host_perf_counter_ns"] = (
        1008_000_000_000
    )
    report, aligned = gaze_metrics(
        [row], windows=[(4, 6)], alignment=alignment, eligible={"TABLET": True}
    )
    assert aligned[0]["analysis_elapsed_s"] == pytest.approx(5)
    assert aligned[0]["session_elapsed_s"] is None
    assert aligned[0]["time_basis"] == "SDK_ACQUISITION_SOFTWARE_ALIGNED"
    assert (
        report["cohorts"]["all_active_m2"]["analysis_sample_ratio_by_aoi"]["TABLET"]
        == 1
    )


def test_held_out_error_is_not_absorbed_by_training_fit():
    pairs = [(100 + i, 1000 + i, 0.0001) for i in range(21)]
    pairs[10] = (110, 1010.015, 0.0001)
    fit = validated_clock_fit(pairs)
    assert fit["holdout_status"] == "FAIL"
    assert fit["holdout_max_error_s"] == pytest.approx(0.015)
    assert fit["residual_max_s"] == pytest.approx(0)


def test_malformed_optional_clock_record_is_explicit_not_an_exception():
    rows = clock_records() + [
        {"record_type": "clock_pair", "host_perf_counter_before_ns": 100}
    ]
    alignment = tobii_alignment(rows, 0, origin_perf_counter_ns=1000_000_000_000)
    assert alignment["status"] == "INVALID_CLOCK_PAIR"
    assert alignment["source_evidence"]["invalid_sdk_pairs"] == 1
    assert map_tobii(alignment, 105_000_000) is None


@pytest.mark.parametrize(
    "pairs",
    [
        [(i, i, 0) for i in range(9)],
        [(i, i * 1.004, 0) for i in range(21)],
        [(i, i, 0.03) for i in range(21)],
        [(i, i, 0) for i in range(10)] + [(i, i, 0) for i in range(11)],
    ],
)
def test_inadequate_span_drift_bracket_and_reset_do_not_pass(pairs):
    assert validated_clock_fit(pairs)["status"] != "SOFTWARE_ALIGNMENT_CHECKED"


def test_failed_alignment_never_falls_back_to_wrong_clock_domain():
    alignment = tobii_alignment([], 0, origin_perf_counter_ns=1000_000_000_000)
    row = gaze(105, 0, "TABLET")
    row["parent_callback_session_s"] = 5
    report, aligned = gaze_metrics(
        [row], windows=[(4, 6)], alignment=alignment, eligible={"TABLET": True}
    )
    assert aligned[0]["analysis_elapsed_s"] is None
    row["sample"]["source_metadata"]["sdk_callback_host_perf_counter_ns"] = (
        1005_000_000_000
    )
    report, aligned = gaze_metrics(
        [row], windows=[(4, 6)], alignment=alignment, eligible={"TABLET": True}
    )
    assert aligned[0]["time_basis"] == "QPC_CALLBACK_APPROXIMATE"
    cohort = report["cohorts"]["all_active_m2"]
    assert cohort["raw_sample_ratio_by_aoi"]["TABLET"] == 1
    assert cohort["analysis_sample_ratio_by_aoi"]["TABLET"] is None


def test_qpc_missing_cannot_interrupt_driver_input_or_module(tmp_path, monkeypatch):
    from src.experiment.automation_interaction import DriverInput
    from src.p8.model import Stage
    from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev

    def broken():
        raise OSError("injected optional clock failure")

    monkeypatch.setattr(
        observation_clock, "time", SimpleNamespace(perf_counter_ns=broken)
    )
    assert observation_clock.observation_ns() is None
    controller, evidence = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.MODULE_1_RUNNING)
    inputs = [DriverInput(throttle=0.4, brake=0.3, steering=0.1)]
    evidence.driver_inputs[:] = inputs
    result = controller.execute_current_module(5)
    assert result.actual_active_drive_s > 0
    assert evidence.observed_driver_inputs == inputs
    assert controller.session.closed is False
    controller.abort("TEST_END")


def test_observation_timestamp_precedes_delayed_serialization(tmp_path, monkeypatch):
    from src.logging.csv_logger import ResearchCsvLogger
    from src.scenario import research_noa_persistence
    from tests.research_noa_fakes import FakeResearchVehicle, FakeResearchWorld
    from tests.test_research_csv_logger import _segment, _study_run

    qpc = iter([100, 200, 900])
    mono = iter([1, 2, 3])
    monkeypatch.setattr(research_noa_persistence, "observation_ns", lambda: next(qpc))
    hero = FakeResearchVehicle()
    world = FakeResearchWorld(hero)
    source = research_noa_persistence.ResearchNoAObservationSource(
        world, hero, monotonic_ns=lambda: next(mono), utc_ns=lambda: 50
    )
    observation = source.get_observation()
    assert world.get_snapshot_count == 1
    assert observation.timestamp.host.perf_counter_ns == 900
    # All observation readings have been consumed: writing must not read again.
    logger = ResearchCsvLogger(_study_run(), tmp_path)
    logger.write_telemetry(_segment(), observation.timestamp, observation.state)
    logger.close()
    with logger.telemetry_path.open() as stream:
        row = next(csv.DictReader(stream))
    assert row["carla_capture_started_perf_counter_ns"] == "100"
    assert row["carla_capture_completed_perf_counter_ns"] == "200"
    assert row["host_perf_counter_ns"] == "900"


def test_sdk_clock_pair_failure_preserves_raw_gaze_and_cleanup(monkeypatch):
    from tests.test_p6_tobii_capture import FakeStreamingSdk, FakeStreamingTracker
    from tools.tobii.capture import CaptureOptions, capture

    tracker = FakeStreamingTracker()
    sdk = FakeStreamingSdk(tracker)

    def broken():
        raise RuntimeError("SDK clock unavailable")

    monkeypatch.setattr(sdk, "get_system_time_stamp", broken)
    rows = []
    capture(sdk, CaptureOptions("FAKE-001", 0.1), rows.append)
    assert any(r["record_type"] == "gaze" for r in rows)
    assert len([r for r in rows if r["record_type"] == "clock_pair_error"]) == 2
    assert set(tracker.removed) == {"gaze", "lost", "errors"}


def test_response_qpc_preserves_thresholds_and_missing_input_is_explicit():
    events = hazard_events()
    display = events[0][2]
    display["flip_completed_perf_counter_ns"] = 1010_000_000_000
    for sample in events[1][2]["samples"]:
        sample["poll_started_perf_counter_ns"] = (
            sample["poll_started_monotonic_ns"] + 1000_000_000_000
        )
        sample["poll_completed_perf_counter_ns"] = (
            sample["poll_completed_monotonic_ns"] + 1000_000_000_000
        )
    legacy = hazard_metrics(events, 0)
    precise = hazard_metrics(events, 1000_000_000_000, precise=True)
    assert precise["status"] == legacy["status"]
    assert precise["first_response_type"] == legacy["first_response_type"]
    assert precise["reaction_time_s"] == legacy["reaction_time_s"] == 1
    assert precise["onset_analysis_elapsed_s"] == 10
    del events[1][2]["samples"][5]["poll_completed_perf_counter_ns"]
    incomplete = hazard_metrics(events, 1000_000_000_000, precise=True)
    assert incomplete["missing_qpc_samples"] == 1
    assert incomplete["analysis_reaction_time_s"] is None
    assert hazard_metrics(events, 0) == legacy


def test_end_to_end_uses_qpc_boundaries_and_does_not_rewrite_sources(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    manifest = {
        "run_id": "R",
        "participant_id": "P",
        "state": "SESSION_COMPLETE",
        "origin": {
            "monotonic_ns": 2000_000_000_000,
            "perf_counter_ns": 1000_000_000_000,
        },
        "study_context": {"module_1_condition": "NO_SURT"},
    }
    (run / "session_manifest.json").write_text(json.dumps(manifest))
    events = [
        {
            "phase": "MODULE_2",
            "event_type": n,
            "event_value": v,
            "session_elapsed_s": old,
            "host_perf_counter_ns": qpc,
        }
        for n, v, old, qpc in [
            ("stage_start", "MODULE_2_RUNNING", 70, 1004_000_000_000),
            ("m2_hazard_preparation_started", "{}", 71, 1005_500_000_000),
            ("stage_end", "MODULE_2_RUNNING:NORMAL_COMPLETION", 72, 1006_000_000_000),
        ]
    ]
    with (run / "research_events_R.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, list(events[0]))
        writer.writeheader()
        writer.writerows(events)
    (run / "research_telemetry_R.csv").write_text(
        "phase,session_elapsed_s,carla_frame,carla_simulation_seconds,host_perf_counter_ns,carla_capture_started_perf_counter_ns,carla_capture_completed_perf_counter_ns\n"
        "MODULE_2,70.5,100,30,1005010000000,1004999000000,1005000000000\n"
    )
    folder = run / "gaze/MODULE_2_RUNNING-test"
    folder.mkdir(parents=True)
    (folder / "raw_gaze.jsonl").write_text(
        "\n".join(json.dumps(r) for r in clock_records())
    )
    (folder / "gaze.jsonl").write_text(json.dumps(gaze(105, 0, "TABLET")))
    (folder / "summary.json").write_text(
        json.dumps(
            {"status": "CAPTURED", "gaze_precheck": {"eligible_aois": {"TABLET": True}}}
        )
    )
    originals = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    build_reports(run, output_directory=tmp_path / "derived")
    assert all(p.read_bytes() == contents for p, contents in originals.items())
    clock = json.loads((tmp_path / "derived/clock_sync.json").read_text())
    assert clock["analysis_active_windows"]["MODULE_2"] == [[4, 6]]
    report = json.loads((tmp_path / "derived/m2_gaze_metrics.json").read_text())[
        "captures"
    ][0]
    assert report["cohorts"]["before_hazard"]["counts"]["received_samples"] == 1
    with (tmp_path / "derived/MODULE_2_vehicle.csv").open() as stream:
        vehicle = next(csv.DictReader(stream))
    assert float(vehicle["session_elapsed_s"]) == 70.5
    assert float(vehicle["analysis_elapsed_s"]) == 5
    assert float(vehicle["record_observed_analysis_elapsed_s"]) == pytest.approx(5.01)
    manifest["components"] = {
        "runtime_faults": [
            {
                "started_perf_counter_ns": 1004_500_000_000,
                "completed_perf_counter_ns": 1005_000_000_000,
            }
        ]
    }
    for row in events:
        row["analysis_elapsed_s"] = (
            row["host_perf_counter_ns"] - 1000_000_000_000
        ) / 1e9
    assert qpc_windows(events, "MODULE_2", None, manifest) == ([(4, 4.5), (5, 6)], [])
    del events[-1]["analysis_elapsed_s"]
    windows, errors = qpc_windows(events, "MODULE_2", 6, manifest)
    assert windows == [] and "MISSING_MODULE_BOUNDARY_QPC" in errors
