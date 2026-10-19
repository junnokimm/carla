import csv
import json
from dataclasses import asdict

import pytest

from src.experiment.automation_interaction import DriverInput
from src.logging.research_event import ResearchEvent
from src.p7.types import P7Error
from src.p8.assignment import development_assignment
from src.p8.manifest import inspect_outputs
from src.p8.model import Mode, Stage
from src.p8.operator_metrics import event_count, reduce_module_run
from src.p8.services import SessionServices
from tests.test_m2_hazard_v1 import prepare, scene_fixture, tick


def test_60hz_hazard_samples_survive_csv_metrics_and_manifest(tmp_path):
    scene, world, clock, _, records, _ = scene_fixture(tmp_path / "scene")
    services = SessionServices(
        tmp_path / "output",
        development_assignment(),
        "M2-CSV-60HZ",
        Mode.DEV_REHEARSAL,
        provenance="FAKE_CARLA_CSV_REGRESSION",
        monotonic_ns=lambda: int(clock() * 1e9) + 1,
        utc_ns=lambda: int(clock() * 1e9) + 1_800_000_000_000_000_000,
    )
    services.stage = Stage.MODULE_2_RUNNING
    scene.services = services
    expected = []

    def record_tick(dt, driver, *, speed=12):
        tick(
            scene,
            world,
            dt,
            speed=speed,
            y=-3.5 if scene.first_geometry else 0,
            driver=driver,
        )
        expected.append(
            {
                "frame": world.frame,
                "input": asdict(driver),
                "m2_active_elapsed_s": clock(),
                "poll_started_monotonic_ns": int(clock() * 1e9) - 20,
                "poll_completed_monotonic_ns": int(clock() * 1e9) - 10,
            }
        )

    def write_event(name, value):
        services.logger.write_event(
            services.segment(), services.stamp(), ResearchEvent(name, value)
        )

    try:
        write_event("initial_state", "AVAILABLE|MANUAL")
        write_event("stage_start", "MODULE_2")
        record_tick(scene.planned - scene.config.preparation_s, DriverInput())
        assert scene.state == "OCCLUDED"
        for index in range(450):
            record_tick(
                1 / 60,
                DriverInput(
                    throttle=(index % 11) / 10,
                    brake=0.3 if scene.first_geometry else 0,
                    stage="MODULE_2",
                ),
            )
        assert scene.first_geometry is not None and scene.first_display is not None
        record_tick(780 - clock(), DriverInput(brake=0.7), speed=0)
        scene.finalize("M2_END")
        write_event("stage_end", "NORMAL_COMPLETION")
    finally:
        if not scene.closed:
            scene.finalize("TEST_CLEANUP")
        services.close()

    # The reader limit stays at Python's default: no global relaxation is needed.
    previous_limit = csv.field_size_limit(131072)
    try:
        path = services.logger.event_path
        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        assert event_count(path) == len(rows)
        assert all(len(row["event_value"]) <= 131072 for row in rows)
        samples = [
            sample
            for row in rows
            if row["event_type"] == "m2_hazard_raw_samples"
            for sample in json.loads(row["event_value"])["samples"]
        ]
        assert [
            {key: sample[key] for key in wanted}
            for sample, wanted in zip(samples, expected, strict=True)
        ] == expected
        assert any("geometry" in sample for sample in samples)
        raw_payloads = [
            json.loads(row["event_value"])
            for row in rows
            if row["event_type"] == "m2_hazard_raw_samples"
        ]
        assert len({payload["batch_id"] for payload in raw_payloads}) == len(
            raw_payloads
        )
        assert all(not payload["retry_after_write_error"] for payload in raw_payloads)
        assert records[0]["raw_storage"]["status"] == "ACKNOWLEDGED"
        assert records[0]["raw_storage"]["acknowledged_sample_count"] == len(expected)
        assert records[0]["raw_storage"]["pending_sample_count"] == 0
        result = reduce_module_run(
            path,
            offset=0,
            stage=Stage.MODULE_2_RUNNING,
            requested_duration_s=780,
            actual_active_drive_s=780,
            user_exited=False,
            planned_duration_s=780,
            runtime_started_session_s=0,
            runtime_closed_session_s=780,
            completion_reason="TIME_LIMIT_REACHED",
            training_completed_early=False,
            nominal_request=True,
        )
        assert result.window_complete
        assert result.actual_active_end_session_s == pytest.approx(780)
        audit = inspect_outputs(
            services.directory,
            services.context,
            complete=False,
            components={"hazard_observation": records[0]},
            provenance=services.provenance,
        )
        assert audit["issues"] == []
        event_file = next(item for item in audit["files"] if item["path"] == path.name)
        assert event_file["data_rows"] == len(rows)
        assert audit["physical_completeness"] == "NOT_HARDWARE_VALIDATED"
    finally:
        csv.field_size_limit(previous_limit)


def test_raw_chunk_write_failure_retains_unconfirmed_samples_in_order(tmp_path):
    scene, _, _, events, _, _ = scene_fixture(tmp_path)
    original = [
        {"frame": index, "input": {"stage": "입력" * 4000}, "poll_completed_ns": index}
        for index in range(4)
    ]
    scene.raw = list(original)
    emit = scene.services.emit
    fail_next = True

    def fail_second_chunk(name, payload):
        nonlocal fail_next
        written = sum(kind == "m2_hazard_raw_samples" for kind, _ in events)
        if name == "m2_hazard_raw_samples" and written == 1 and fail_next:
            fail_next = False
            raise OSError("injected chunk write failure")
        emit(name, payload)

    scene.services.emit = fail_second_chunk
    try:
        with pytest.raises(OSError, match="injected chunk write failure"):
            scene._flush_raw()
        assert scene.raw == original[1:]
        scene._flush_raw()
        assert scene.raw == []
        payloads = [
            payload for name, payload in events if name == "m2_hazard_raw_samples"
        ]
        assert [
            sample for payload in payloads for sample in payload["samples"]
        ] == original
        assert all(
            len(json.dumps(payload, allow_nan=False)) < 131072 for payload in payloads
        )
    finally:
        scene.finalize("USER_EXIT")


def test_csv_flush_failure_retries_the_identical_identifiable_batch(tmp_path):
    scene, _, clock, _, records, _ = scene_fixture(tmp_path / "scene")
    services = SessionServices(
        tmp_path / "output",
        development_assignment(),
        "M2-CSV-FLUSH-FAILURE",
        Mode.DEV_REHEARSAL,
        provenance="INJECTED_CSV_FLUSH_FAILURE",
        monotonic_ns=lambda: int(clock() * 1e9) + 1,
        utc_ns=lambda: int(clock() * 1e9) + 1_800_000_000_000_000_000,
    )
    services.stage = Stage.MODULE_2_RUNNING
    scene.services = services
    original = [
        {"frame": index, "input": {"stage": "입력" * 1000}, "poll_completed_ns": index}
        for index in range(3)
    ]
    scene.raw = original[:2]
    event_file = services.logger._event_file
    header_bytes = services.logger.event_path.stat().st_size

    class FailFirstFlush:
        failed = False

        def flush(self):
            if not self.failed:
                self.failed = True
                raise OSError("injected flush after successful writerow")
            event_file.flush()

        def close(self):
            event_file.close()

    # The actual csv.writer stays bound to event_file, so writerow succeeds
    # before the logger's explicit flush fails. This reproduces uncertain writes.
    services.logger._event_file = FailFirstFlush()
    try:
        with pytest.raises(OSError, match="flush after successful writerow"):
            scene._flush_raw()
        assert services.logger.event_path.stat().st_size > header_bytes
        assert scene.raw == original[:2]
        # Later observations must not change the content of the pending batch.
        scene.raw.append(original[2])
        scene.last_geometry = {"frame": 99, "time": 1.5}
        scene._flush_raw()
        assert scene.raw == []
        scene.finalize("M2_END")
    finally:
        if not scene.closed:
            scene.finalize("TEST_CLEANUP")
        services.close()

    with services.logger.event_path.open(encoding="utf-8", newline="") as source:
        payloads = [
            json.loads(row["event_value"])
            for row in csv.DictReader(source)
            if row["event_type"] == "m2_hazard_raw_samples"
        ]
    first, retry, following = payloads
    assert [sample["frame"] for row in payloads for sample in row["samples"]] == [
        0,
        1,
        0,
        1,
        2,
    ]
    assert first["batch_id"] == retry["batch_id"] != following["batch_id"]
    assert first["write_attempt_id"] != retry["write_attempt_id"]
    assert first["retry_after_write_error"] is False
    assert retry["retry_after_write_error"] is True
    assert following["retry_after_write_error"] is False
    assert first["sample_start_index"] == retry["sample_start_index"] == 0
    assert first["sample_count"] == retry["sample_count"] == 2
    assert first["samples"] == retry["samples"] == original[:2]
    assert first["geometry_latest"] is retry["geometry_latest"] is None
    assert following["sample_start_index"] == 2
    assert following["samples"] == original[2:]
    assert following["geometry_latest"] == scene.last_geometry
    unique_batches = {row["batch_id"]: row for row in payloads}
    assert [
        sample for row in unique_batches.values() for sample in row["samples"]
    ] == original
    storage = records[0]["raw_storage"]
    assert storage["status"] == "ACKNOWLEDGED_AFTER_RETRY"
    assert storage["acknowledged_sample_count"] == len(original)
    assert storage["pending_sample_count"] == 0
    assert storage["possible_duplicate_batches"] is True
    assert storage["duplicate_detection_key"] == "batch_id"
    assert storage["write_errors"][0]["write_attempt_id"] == first["write_attempt_id"]
    assert storage["write_errors"][0]["outcome"] == "WRITE_UNCONFIRMED"


def test_final_raw_flush_failure_records_partial_failure_after_actor_cleanup(tmp_path):
    scene, world, _, events, records, _ = scene_fixture(tmp_path)
    prepare(scene, world)
    original = list(scene.raw)
    emit = scene.services.emit

    def fail_raw(name, payload):
        if name == "m2_hazard_raw_samples":
            raise OSError("injected final raw flush failure")
        emit(name, payload)

    scene.services.emit = fail_raw
    with pytest.raises(OSError, match="final raw flush failure"):
        scene.finalize("M2_END")
    scene.finalize("LATE_CALLBACK")
    assert scene.closed and len(records) == 1
    assert scene.raw == original
    assert not world.callbacks and not scene.backend.owned.actors
    assert world.vehicle.destroy_count == 0
    record = records[0]
    assert record["execution_status"] == "FAILED"
    assert record["partial_data"] is True
    assert record["error"] == "OSError:injected final raw flush failure"
    assert record["end_reason"] == "M2_END"
    assert record["cleanup"] == {"failures": [], "remaining_actor_ids": []}
    assert record["raw_storage"]["status"] == "WRITE_UNCONFIRMED"
    assert record["raw_storage"]["pending_sample_count"] == len(original)
    assert record["finalization_errors"] == [
        {"step": "raw_flush", "error": "OSError:injected final raw flush failure"}
    ]
    end = next(payload for name, payload in events if name == "m2_hazard_end")
    assert end["execution_status"] == "FAILED" and end["partial_data"] is True


def test_primary_error_survives_raw_end_and_cleanup_failures(tmp_path):
    scene, world, _, _, records, _ = scene_fixture(tmp_path)
    prepare(scene, world)
    lead = scene.backend.lead
    destroy = lead.destroy
    lead.destroy = lambda: False
    emit = scene.services.emit

    def fail_storage(name, payload):
        if name in ("m2_hazard_raw_samples", "m2_hazard_end"):
            raise OSError(f"injected failure:{name}")
        emit(name, payload)

    scene.services.emit = fail_storage
    primary = P7Error("ORIGINAL_DRIVER_LOOP_FAILURE")
    try:
        scene.finalize("ERROR", primary)
        record = records[0]
        assert record["error"] == "P7Error:ORIGINAL_DRIVER_LOOP_FAILURE"
        assert record["execution_status"] == "FAILED"
        assert record["partial_data"] is True
        assert record["cleanup"]["remaining_actor_ids"] == [lead.id]
        assert [failure["step"] for failure in record["finalization_errors"]] == [
            "cleanup",
            "raw_flush",
            "end_event",
        ]
        assert any("CLEANUP_INCOMPLETE" in note for note in primary.__notes__)
        assert any("m2_hazard_raw_samples" in note for note in primary.__notes__)
        assert any("m2_hazard_end" in note for note in primary.__notes__)
        assert not world.callbacks and world.vehicle.destroy_count == 0
    finally:
        lead.destroy = destroy
        scene.backend.owned.close()


def test_end_event_flush_failure_updates_manifest_record(tmp_path):
    scene, world, _, _, records, _ = scene_fixture(tmp_path)
    scene.raw = [{"frame": 42, "poll_completed_monotonic_ns": 12345}]
    emit = scene.services.emit

    def fail_end(name, payload):
        if name == "m2_hazard_end":
            raise OSError("injected end event flush failure")
        emit(name, payload)

    scene.services.emit = fail_end
    with pytest.raises(OSError, match="end event flush failure"):
        scene.finalize("M2_END")
    assert scene.raw == []
    assert not world.callbacks and not scene.backend.owned.actors
    record = records[0]
    assert record["raw_storage"]["status"] == "ACKNOWLEDGED"
    assert record["execution_status"] == "FAILED"
    assert record["partial_data"] is True
    assert record["error"] == "OSError:injected end event flush failure"
    assert record["finalization_errors"] == [
        {"step": "end_event", "error": "OSError:injected end event flush failure"}
    ]
