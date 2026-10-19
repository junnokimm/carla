"""Read existing pilot CSVs; emit compact evidence without changing input files.

Run from the repository root with the P8 Python interpreter and -B.
Printed JSON can be redirected to a new report file. This never connects to CARLA.
"""

import csv
import json
from collections import Counter
from pathlib import Path

csv.field_size_limit(100_000_000)
RUNS = (
    "bdb31eb6b3874f43b93a3313b4b9822f",
    "42c0b587e1e64ca0b08deaf0742ca01c",
    "9813fc1585164f4494dc4f638e845d3a",
    "ad1d1304cc994408a5aba3ff697334b2",
)


def analyze(suffix):
    run = "P8-PILOT-" + suffix
    directory = Path("data/p8_operator") / run
    counts, reasons = Counter(), Counter()
    performance, hazard = [], []
    last = None
    with (directory / f"research_events_{run}.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        for record_number, row in enumerate(csv.DictReader(stream), 2):
            name = row["event_type"]
            counts[name] += 1
            last = {
                key: row[key]
                for key in ("event_type", "host_utc_ns", "session_elapsed_s", "module")
            }
            last["csv_record"] = record_number
            if name == "performance_summary":
                performance.append(
                    {
                        "csv_record": record_number,
                        "module": row["module"] or row["phase"],
                        "summary": json.loads(row["event_value"]),
                    }
                )
            elif name.startswith("m2_hazard_") and name not in (
                "m2_hazard_raw_samples",
                "m2_hazard_camera_basis",
            ):
                payload = json.loads(row["event_value"])
                if name == "m2_hazard_preparation_deferred":
                    reasons[payload["reason"]] += 1
                else:
                    hazard.append(
                        {"csv_record": record_number, "type": name, "payload": payload}
                    )
    return {
        "run_id": run,
        "manifest": json.loads((directory / "session_manifest.json").read_text()),
        "export_exists": (directory.parent / "_exports" / f"{run}.zip").exists(),
        "last_event": last,
        "event_counts": dict(counts),
        "preparation_deferred_reasons": dict(reasons),
        "performance": performance,
        "hazard": hazard,
    }


if __name__ == "__main__":
    print(json.dumps([analyze(run) for run in RUNS], indent=2))
