from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from src.p6.aoi import AoiLayout
from src.p6.clock import SessionOrigin
from src.p6.clock_evidence import read_clock_evidence
from src.p6.tobii.protocol import TobiiSampleDecoder
from src.p6.validation.mapping import WindowGeometry
from src.p6.validation.observation import observe
from src.p6.validation.segments import TARGETS, SegmentSequence
from src.p6.validation.summary import Json, TargetStats
from src.p6.writer import GazeSidecarWriter
from tools.tobii.sdk import TobiiError


def analyze(directory: Path, layout: AoiLayout, sequence: SegmentSequence, *,
            geometry: WindowGeometry | None, operator_confirmed: bool,
            raw_path: Path | None = None) -> dict[str, Json]:
    raw_path = directory / "raw_gaze.jsonl" if raw_path is None else raw_path
    origin = SessionOrigin(sequence.origin_ns, 0)
    errors: list[str] = []
    device: dict[str, Json] | None = None
    source_status = "NO_TERMINAL_STATUS"
    dropped = 0
    with raw_path.open(encoding="utf-8") as raw:
        for index, line in enumerate(raw):
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise TobiiError("INVALID_RECORD", "JSON object required")
                match record.get("record_type"):
                    case "session_origin":
                        origin = SessionOrigin(record["monotonic_ns"], record["utc_ns"])
                    case "identity":
                        device = record["device"]
                    case "status":
                        source_status = record["status"]
                        dropped += record.get("dropped_count", 0)
            except (ValueError, KeyError, TypeError, TobiiError) as error:
                errors.append(f"line {index + 1}: {error}")
    clock_evidence = read_clock_evidence(raw_path)
    mapping = clock_evidence.fit(origin)
    stats = {target: TargetStats() for target in TARGETS}
    decoder = TobiiSampleDecoder()
    writer = GazeSidecarWriter(path=directory / "p6_gaze.jsonl", run_id=sequence.run_id,
                              origin=origin, mapping=mapping, layout=layout)
    writer.start()
    try:
        with raw_path.open(encoding="utf-8") as raw, (directory / "gaze_with_candidate_aoi.jsonl").open("x", encoding="utf-8") as output:
            for line in raw:
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict) or record.get("record_type") != "gaze":
                        continue
                    envelope = decoder.decode(line)
                except (ValueError, TobiiError) as error:
                    errors.append(f"gaze decode: {error}")
                    continue
                sample = envelope.sample
                writer.capture(sample, receive_monotonic_ns=envelope.host_monotonic_ns,
                               receive_utc_ns=envelope.host_wall_time_ns)
                acquisition = mapping.to_session_seconds(sample.source_timestamp)
                sample_ns = (envelope.host_monotonic_ns if acquisition is None else
                             origin.monotonic_ns + round(acquisition * 1e9))
                window = sequence.segment_at(sample_ns)
                observation = observe(sample, layout, geometry=geometry,
                                      operator_confirmed=operator_confirmed and window is not None)
                if window is None:
                    observation = replace(observation, candidate="NOT_MEASURED", missing_reason="OUTSIDE_SEGMENT")
                if window is not None:
                    stats[window.target].add(window.target, observation)
                normalized, client = observation.normalized, observation.client
                row = {
                    "validation_run_id": sequence.run_id,
                    "expected_target": None if window is None else window.target,
                    "segment_index": None if window is None else window.index,
                    "segment_start_session_s": None if window is None else (window.start_ns - origin.monotonic_ns) / 1e9,
                    "segment_elapsed_s": None if window is None else (sample_ns - window.start_ns) / 1e9,
                    "session_elapsed_s": (sample_ns - origin.monotonic_ns) / 1e9,
                    "sample_time_basis": "SDK_SYSTEM_VALIDATED_MAPPING" if acquisition is not None else "SDK_CALLBACK_RECEIPT_DIAGNOSTIC_NOT_ACQUISITION",
                    "acquisition_session_seconds": acquisition,
                    "estimated_session_seconds": mapping.estimate_session_seconds(sample.source_timestamp),
                    "segment_assignment_verified": mapping.verified,
                    "host_monotonic_ns": envelope.host_monotonic_ns,
                    "host_wall_time_ns": envelope.host_wall_time_ns,
                    "gaze_x": None if normalized is None else normalized.x,
                    "gaze_y": None if normalized is None else normalized.y,
                    "client_x": None if client is None else client.x,
                    "client_y": None if client is None else client.y,
                    "client_coordinate_space": "PYGAME_RENDER_SURFACE",
                    "left": asdict(sample.left), "right": asdict(sample.right),
                    "left_validity": sample.source_metadata["left_validity"],
                    "right_validity": sample.source_metadata["right_validity"],
                    "any_eye_valid": observation.any_eye_valid,
                    "both_eye_valid": observation.both_eye_valid,
                    "classification_input_valid": observation.classification_input_valid,
                    "candidate_aoi": observation.candidate,
                    "production_aoi": observation.production_aoi,
                    "missing_reason": observation.missing_reason,
                    "source_timestamp": sample.source_timestamp, "source_unit": sample.source_unit,
                    "source_sequence": sample.source_sequence,
                    **sample.source_metadata,
                }
                output.write(json.dumps(row, allow_nan=False) + "\n")
                output.flush()
    finally:
        writer.close()
    if writer.worker_error is not None:
        errors.append(str(writer.worker_error))
    return {
        "validation_run_id": sequence.run_id,
        "status": sequence.phase if not errors else "ANALYSIS_ERROR",
        "targets": {target: value.result() for target, value in stats.items()},
        "segments": [asdict(window) for window in sequence.windows],
        "source_status": source_status, "source_dropped_samples": dropped,
        "device": device, "clock_mapping": mapping.provenance.as_record(),
        "clock_evidence": clock_evidence.report(), "software_clock_fitted": mapping.fitted,
        "segment_assignment_verified": mapping.verified,
        "targets_semantics": "RECEIPT_TIME_DIAGNOSTIC_NOT_ACQUISITION_TRUTH" if not mapping.verified else "VALIDATED_MAPPING",
        "analysis_errors": errors, "candidate_aoi_version": layout.version,
        "fusion_policy": "VALID_EYE_MEAN_FOR_VALIDATION_ONLY",
        "hit_ratio_denominator": "ALL_SAMPLES_IN_EXPECTED_SEGMENT",
        "missing_hit_definition": "NEITHER_EYE_SDK_VALID",
        "operator_etm_display_confirmed": operator_confirmed,
        "display_mapping_verified": False, "client_mapping_verified": False,
        "aoi_validated": False, "hardware_validated": False, "tablet_status": "NOT_TESTED",
    }
