from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from src.experiment.context import StudyRunContext
from src.p7.types import Json


def inspect_outputs(
    directory: Path,
    context: StudyRunContext,
    *,
    complete: bool,
    components: dict[str, Json],
    provenance: str = "DEV_STUB",
) -> dict[str, Json]:
    issues: list[str] = []
    files = []
    for path in sorted(directory.iterdir()):
        if (
            not path.is_file()
            or path.name.startswith(".")
            or path.name == "session_manifest.json"
        ):
            continue
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        size = path.stat().st_size
        if not size:
            issues.append("EMPTY_FILE:" + path.name)
        count = 0
        if path.suffix == ".csv":
            with path.open(newline="", encoding="utf-8") as source:
                for row in csv.DictReader(source):
                    count += 1
                    if (
                        row["participant_id"] != context.participant_id
                        or row["run_id"] != context.study_run_id
                    ):
                        issues.append("CSV_CONTEXT_MISMATCH:" + path.name)
        elif path.suffix == ".jsonl":
            with path.open(encoding="utf-8") as source:
                for line in source:
                    row = json.loads(line)
                    count += row.get("record_type") != "metadata"
                    if row.get("participant_id") != context.participant_id:
                        issues.append("JSONL_PARTICIPANT_MISMATCH:" + path.name)
                    if row.get("run_id") != context.study_run_id:
                        issues.append("JSONL_RUN_MISMATCH:" + path.name)
        files.append(
            {
                "path": path.name,
                "bytes": size,
                "sha256": digest,
                "data_rows": count,
                "participant_id": context.participant_id,
                "run_id": context.study_run_id,
                "provenance": provenance,
                "hardware_validation": "NOT_HARDWARE_VALIDATED",
            }
        )
    if complete:
        expected = {
            f"research_events_{context.study_run_id}.csv",
            f"research_telemetry_{context.study_run_id}.csv",
            f"p6_gaze_{context.study_run_id}.jsonl",
            f"p6_tablet_{context.study_run_id}.jsonl",
            "DEV_STUB_NOT_A_VIDEO.bin",
        }
        present = {item["path"] for item in files}
        issues.extend("MISSING_FILE:" + name for name in sorted(expected - present))
        issues.extend(
            "NO_DATA_ROWS:" + item["path"]
            for item in files
            if item["path"] in expected
            and not item["path"].endswith(".bin")
            and item["data_rows"] == 0
        )
        if components.get("exit_slots") != {
            "TRAINING": 1,
            "MODULE_1": 1,
            "MODULE_2": 1,
        }:
            issues.append("EXIT_SLOT_COMPLETENESS")
        if (
            components.get("hazard_count") != 1
            or components.get("hazard_state") != "COMPLETED"
        ):
            issues.append("HAZARD_COMPLETENESS")
        if not components.get("gaze_samples"):
            issues.append("NO_DEV_GAZE_SAMPLES")
        if components.get("owned_resource_tokens"):
            issues.append("OWNED_RESOURCES_REMAIN")
    return {
        "files": files,
        "issues": issues,
        "software_artifacts_complete": complete and not issues,
        "physical_completeness": "NOT_HARDWARE_VALIDATED",
    }
