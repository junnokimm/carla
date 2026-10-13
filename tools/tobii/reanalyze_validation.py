# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# Run with .venv Python: python -m tools.tobii.reanalyze_validation --help
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from math import isfinite
from pathlib import Path
from statistics import median

from src.p6.aoi import Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.validation.analysis import analyze
from src.p6.validation.mapping import WindowGeometry
from src.p6.validation.segments import SegmentSequence, SegmentWindow


def hashes(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            with path.open("rb") as source:
                result[path.name] = hashlib.file_digest(source, "sha256").hexdigest()
    return result


def reanalyze(source: Path, output: Path, aoi: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents:
        raise ValueError("output must be a new directory outside the archive")
    before = hashes(source)
    saved = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    evidence = json.loads((source / "mapping_evidence.json").read_text(encoding="utf-8"))
    with (source / "raw_gaze.jsonl").open(encoding="utf-8") as raw:
        origin = json.loads(next(raw))
    sequence = SegmentSequence(saved["validation_run_id"], origin["monotonic_ns"])
    starts = {}
    aborted = False
    with (source / "events.jsonl").open(encoding="utf-8") as events:
        for line in events:
            event = json.loads(line)
            index = event["segment_index"]
            if event["event"] == "START":
                starts[index] = event
            elif event["event"] in ("END", "ABORT"):
                aborted |= event["event"] == "ABORT"
                if index in starts:
                    start = starts.pop(index)
                    sequence.windows.append(SegmentWindow(index, start["expected_target"], start["host_monotonic_ns"],
                                                          event["host_monotonic_ns"], event["event"] == "END"))
    sequence.phase = "DONE" if not aborted and len(sequence.windows) == 5 else "ABORTED"
    observed = evidence["window_geometry"]
    geometry = WindowGeometry(Rect(**observed["desktop"]), Rect(**observed["monitor"]), Rect(**observed["client"]),
                              tuple(observed["surface_size"]), observed["hwnd"], observed["monitor_name"], observed["dpi"])
    output.mkdir(parents=True, exist_ok=False)
    report = analyze(output, load_aoi_layout(aoi), sequence, geometry=geometry,
                     operator_confirmed=evidence["operator_etm_display_confirmed"], raw_path=source / "raw_gaze.jsonl")
    legacy = saved["clock_mapping"]
    rate, offset = legacy["rate_host_ns_per_source_unit"], legacy["offset_host_ns"]
    legacy_counts, receipt_counts = Counter(), Counter()
    differences = []
    with (source / "raw_gaze.jsonl").open(encoding="utf-8") as raw:
        for line in raw:
            record = json.loads(line)
            if record.get("record_type") != "gaze":
                continue
            sdk = json.loads(record["raw_sdk_json"])["system_time_stamp"]
            receipt = record["host_monotonic_ns"]
            received_window = sequence.segment_at(receipt)
            if received_window is not None:
                receipt_counts[received_window.target] += 1
            if rate is not None and offset is not None and isfinite(rate) and isfinite(offset):
                old_host = offset + rate * sdk
                differences.append((receipt, (receipt - old_host) / 1e6))
                old_window = sequence.segment_at(round(old_host))
                if old_window is not None:
                    legacy_counts[old_window.target] += 1
    first = [delta for stamp, delta in differences if stamp <= differences[0][0] + 1e9] if differences else []
    last = [delta for stamp, delta in differences if stamp >= differences[-1][0] - 1e9] if differences else []
    diagnostics = {
        "legacy_rate_ns_per_us": rate, "legacy_drift_ppm": legacy["drift_ppm"],
        "callback_minus_legacy_start_median_ms": median(first) if first else None,
        "callback_minus_legacy_end_median_ms": median(last) if last else None,
        "callback_minus_legacy_last_sample_ms": differences[-1][1] if differences else None,
        "legacy_mapping_diagnostic_counts": dict(legacy_counts),
        "callback_receipt_diagnostic_counts": dict(receipt_counts),
        "callback_receipt_is_acquisition_truth": False,
        "new_clock_fit": report["clock_mapping"], "clock_evidence": report["clock_evidence"],
        "new_method_accuracy": "NOT_VALIDATED",
    }
    after = hashes(source)
    if before != after:
        raise RuntimeError("archive changed during analysis; no source file was written by this command")
    report.update({"offline_source": str(source), "original_data_modified": False,
                   "new_method_accuracy": "NOT_VALIDATED", "diagnostics": diagnostics})
    with (output / "analysis_summary.json").open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, allow_nan=False)
    with (output / "source_manifest.json").open("x", encoding="utf-8") as target:
        json.dump({"source": str(source), "sha256_before": before, "sha256_after": after,
                   "source_unchanged": True}, target, indent=2)
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline archive diagnostics; never writes into the input run; no hardware")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--aoi", type=Path, default=Path("config/p6_aoi_candidate.json"))
    args = parser.parse_args(argv)
    try:
        report = reanalyze(args.input_dir, args.output_dir, args.aoi)
        print(json.dumps({"output_dir": str(args.output_dir), **report}, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(json.dumps({"status": "ANALYSIS_FAILED", "detail": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
