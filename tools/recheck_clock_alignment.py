"""Read-only source replay; legacy acquisition correction is diagnostic only."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

from src.p8.collection_clock import fit_clock, map_time
from src.p8.collection_gaze import gaze_metrics
from src.p8.collection_io import records, save_json
from src.p8.collection_report import build_reports


def fingerprints(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(directory))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


def diagnostic_legacy_comparison(source, derived):
    """The legacy parent bridge fails precision checks: no new 'pass' is claimed."""
    clock = json.loads((derived / "clock_sync.json").read_text())
    before = json.loads((derived / "m2_gaze_metrics.json").read_text())
    results, errors = [], []
    for report in before["captures"]:
        folder = source / report["source"]
        alignment = clock["tobii"][folder.name]
        if alignment.get("session_clock") == "SHARED_QPC":
            continue
        sdk = alignment["sdk_to_shared_qpc_holdout"]
        parent = alignment["shared_qpc_to_session"]
        if sdk["status"] != "SOFTWARE_ALIGNMENT_CHECKED" or parent.get("rate") is None:
            results.append(
                {"source": report["source"], "status": "INSUFFICIENT_LEGACY_EVIDENCE"}
            )
            continue
        shifts, callback_delays = [], []
        boundary_changes = {"active_m2": 0, "before_hazard": 0}
        windows, hazard = report["windows"], report["hazard_exclusion_start_s"]

        def membership(t, windows=windows, hazard=hazard):
            active = t is not None and any(a <= t < b for a, b in windows)
            return active, active and (hazard is None or t < hazard)

        def estimated_rows(
            folder=folder,
            sdk=sdk,
            parent=parent,
            shifts=shifts,
            callback_delays=callback_delays,
            boundary_changes=boundary_changes,
            membership=membership,
        ):
            for row in records(folder / "gaze.jsonl", errors):
                if row.get("record_type") != "gaze":
                    continue
                metadata = row.get("sample", {}).get("source_metadata", {})
                system_us = metadata.get("system_timestamp_raw")
                if system_us is None and metadata.get("raw_sdk_json"):
                    system_us = json.loads(metadata["raw_sdk_json"]).get(
                        "system_time_stamp"
                    )
                qpc = None if system_us is None else map_time(sdk, system_us / 1e6)
                estimate = None
                if (
                    qpc is not None
                    and parent["source_start_s"] <= qpc <= parent["source_end_s"]
                ):
                    estimate = parent["target_anchor_s"] + parent["rate"] * (
                        qpc - parent["source_anchor_s"]
                    )
                previous = row.get("parent_callback_session_s")
                if estimate is not None and previous is not None:
                    shifts.append(estimate - previous)
                callback = metadata.get("sdk_callback_host_perf_counter_ns")
                if qpc is not None and callback is not None:
                    callback_delays.append(callback / 1e9 - qpc)
                old, new = membership(previous), membership(estimate)
                boundary_changes["active_m2"] += old[0] != new[0]
                boundary_changes["before_hazard"] += old[1] != new[1]
                # Use the reducer's explicitly approximate path, with this
                # override confined to an in-memory diagnostic copy.
                yield {**row, "parent_callback_session_s": estimate}

        approximate, _ = gaze_metrics(
            estimated_rows(),
            windows=windows,
            alignment={
                "sdk_to_shared_qpc": fit_clock([]),
                "shared_qpc_to_session": fit_clock([]),
            },
            eligible={},
            hazard_start=hazard,
        )

        def spread(values):
            ordered = sorted(values)
            return (
                None
                if not values
                else {
                    "count": len(values),
                    "min_s": ordered[0],
                    "median_s": median(values),
                    "p95_s": ordered[round(0.95 * (len(values) - 1))],
                    "max_s": ordered[-1],
                }
            )

        results.append(
            {
                "source": report["source"],
                "status": "LEGACY_DIAGNOSTIC_ONLY_NOT_PRECISION_VALIDATED",
                "sdk_qpc_holdout_max_error_s": sdk["holdout_max_error_s"],
                "parent_bridge_status": parent["status"],
                "parent_bridge_max_residual_s": parent["residual_max_s"],
                "acquisition_estimate_minus_previous_timestamp": spread(shifts),
                "sdk_callback_minus_acquisition": spread(callback_delays),
                "boundary_membership_changes": boundary_changes,
                "before": report["cohorts"],
                "diagnostic_after": approximate["cohorts"],
                "warning": "OLD_VEHICLE_EVENTS_HAVE_NO_QPC;COARSE_PARENT_FIT_USED_ONLY_TO_ESTIMATE_SENSITIVITY;NO_ORIGINALS_OR_PASS_FLAGS_CHANGED",
            }
        )
    return {"comparisons": results, "read_errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or source in output.parents or output.exists():
        parser.error("Use a new output folder outside the source run.")
    before = fingerprints(source)
    output.mkdir(parents=True)
    build_reports(source, output_directory=output / "collection")
    comparison = diagnostic_legacy_comparison(source, output / "collection")
    after = fingerprints(source)
    comparison.update(
        source=str(source),
        source_file_count=len(before),
        source_hashes_unchanged=before == after,
    )
    save_json(output / "source_sha256.json", before)
    save_json(output / "comparison.json", comparison)
    print(
        json.dumps(
            {
                "output": str(output),
                "source_hashes_unchanged": before == after,
                "source_file_count": len(before),
                "read_errors": comparison["read_errors"],
            }
        )
    )
    return 0 if before == after and not comparison["read_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
