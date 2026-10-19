"""Post-process a stopped worker's durable records without CARLA/SDL imports.

Raw files are never repaired in place. Incomplete records stay in the evidence;
only complete, structurally valid records contribute to recovery summaries.
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.p8.operator_results import export_operator_results

REGISTRATION_ENV = "P8_PILOT_SUPERVISOR_REGISTRATION"
TOKEN_ENV = "P8_PILOT_SUPERVISOR_TOKEN"
TERMINAL = {"SESSION_COMPLETE", "ABORTED"}


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".recovery-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def register_supervised_run(directory: Path, participant_id: str):
    registration = os.environ.get(REGISTRATION_ENV)
    if registration:
        atomic_json(Path(registration), {"worker_pid": os.getpid(),
                    "launch_token": os.environ[TOKEN_ENV],
                    "directory": str(directory.resolve()), "run_id": directory.name,
                    "participant_id": participant_id})


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def _csv_summary(path, run_id):
    result = {"rows": 0, "invalid_rows": 0, "last_session_s": None,
              "stage_windows": {}, "last_hazard_events": {}, "unterminated_tail": False}
    # Hazard raw batches exceed csv's default 128 KiB field limit.
    old_limit = csv.field_size_limit(16 * 1024 * 1024)
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            def terminated_lines():
                for line in stream:
                    if not line.endswith("\n"):
                        result["unterminated_tail"] = True
                        return
                    yield line
            reader = csv.DictReader(terminated_lines(), strict=True)
            for row in reader:
                if None in row or any(v is None for v in row.values()) or row.get("run_id") != run_id:
                    result["invalid_rows"] += 1
                    continue
                result["rows"] += 1
                stamp = _number(row.get("session_elapsed_s"))
                if stamp is not None:
                    result["last_session_s"] = max(result["last_session_s"] or 0, stamp)
                    stage = row.get("module") or row.get("phase") or "UNKNOWN"
                    window = result["stage_windows"].setdefault(stage, {"first_session_s": stamp, "last_session_s": stamp})
                    window["first_session_s"] = min(window["first_session_s"], stamp)
                    window["last_session_s"] = max(window["last_session_s"], stamp)
                event = row.get("event_type", "")
                if event.startswith("m2_hazard_") and event != "m2_hazard_raw_samples":
                    # Evidence only, never reconstruct an unobserved hazard outcome.
                    result["last_hazard_events"][event] = {
                        "session_elapsed_s": stamp, "event_value": row.get("event_value")}
    except (csv.Error, UnicodeError) as error:
        result["invalid_rows"] += 1
        result["read_error"] = f"{type(error).__name__}:{error}"
    finally:
        csv.field_size_limit(old_limit)
    if result["unterminated_tail"] and "read_error" not in result:
        result["invalid_rows"] += 1
    return result


def _jsonl_summary(path):
    result = {"valid_records": 0, "invalid_records": 0, "invalid_line_numbers": [],
              "seconds_by_state": {}, "last_session_s": None,
              "tail_policy": "NO_DURATION_INFERRED_AFTER_LAST_SAVED_INTERVAL",
              "analysis_eligible": False}
    durations = Counter()
    previous_end = None
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("JSON object required")
            except (ValueError, UnicodeError):
                result["invalid_records"] += 1
                if len(result["invalid_line_numbers"]) < 100:
                    result["invalid_line_numbers"].append(line_number)
                continue
            result["valid_records"] += 1
            if row.get("record_type") == "gaze_interval":
                duration = _number(row.get("duration_s"))
                start, end = _number(row.get("start_clock_ns")), _number(row.get("end_clock_ns"))
                if (duration is None or start is None or end is None or end < start
                        or (previous_end is not None and start < previous_end)
                        or abs(duration - (end - start) / 1e9) > 1e-6):
                    result["invalid_records"] += 1
                    continue
                previous_end = end
                durations[str(row.get("state", "UNKNOWN"))] += duration
                stamp = _number(row.get("session_end_s"))
                if stamp is not None:
                    result["last_session_s"] = max(result["last_session_s"] or 0, stamp)
    result["seconds_by_state"] = dict(durations)
    return result


def recover_stopped_run(directory: Path, *, returncode: int, worker_exited: bool,
                        diagnostics: Path | None = None):
    """Caller must have waited for its own worker; never inspect/stop CARLA here."""
    if not worker_exited:
        raise ValueError("RECOVERY_REQUIRES_EXITED_WORKER")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("RECOVERY_CANNOT_FOLLOW_LINKS")
    manifest_path = directory / "session_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "run_id": directory.name, "state": "UNKNOWN", "stages": [], "components": {}}
    if manifest.get("run_id") != directory.name:
        raise ValueError("RECOVERY_RUN_ID_MISMATCH")
    if manifest.get("state") in TERMINAL:
        # Idempotent, including a retry after failed ZIP publication.
        return export_operator_results(directory, closed=True)
    recovery = directory / "crash_recovery"
    recovery.mkdir(exist_ok=True)
    backup = recovery / "session_manifest.before_recovery.json"
    if manifest_path.exists() and not backup.exists():
        shutil.copyfile(manifest_path, backup)
    if diagnostics is not None:
        shutil.copytree(diagnostics, recovery / "diagnostics", dirs_exist_ok=True)
    pending_summaries = {}
    # Also preserve interrupted prechecks, not just the M2 driving sidecar.
    for pending in sorted(directory.rglob(".gaze_pending")):
        if recovery in pending.parents:
            continue
        relative = pending.relative_to(directory)
        target = recovery / "pending_gaze" / relative.parent / "gaze_pending"
        shutil.copytree(pending, target, dirs_exist_ok=True)
        for source in sorted(target.rglob("*.jsonl")):
            pending_summaries[source.relative_to(directory).as_posix()] = _jsonl_summary(source)
    csv_summaries = {path.name: _csv_summary(path, directory.name)
                     for path in sorted(directory.glob("*.csv"))}
    last = max((item["last_session_s"] for item in csv_summaries.values()
                if item["last_session_s"] is not None), default=None)
    report = {
        "schema_version": 1, "run_id": directory.name, "status": "RECOVERED_PARTIAL",
        "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
        "worker_exit_code": returncode, "worker_exit_code_hex": f"0x{returncode & 0xffffffff:08X}",
        "interrupted_stage": manifest.get("state"), "last_saved_session_s": last,
        "actual_process_exit_session_s": None,
        "raw_files_modified": False, "unsaved_memory_recoverable": False,
        "simulator_cleanup": "NOT_ATTEMPTED_REQUIRES_OPERATOR_CHECK",
        "hazard_outcome": "USE_SAVED_EVENTS_ONLY_NO_OUTCOME_INFERRED",
        "csv": csv_summaries, "pending_gaze": pending_summaries,
    }
    atomic_json(recovery / "report.json", report)
    for stage in manifest.get("stages", []):
        if stage.get("status") == "RUNNING":
            stage.update(status="ABORTED", reason="WORKER_PROCESS_EXITED",
                         last_saved_session_s=last, duration_s=None, end_session_s=None)
    manifest.update(state="ABORTED", completion_status="ABORTED",
                    abort_reason="WORKER_PROCESS_EXITED", analysis_eligible=False,
                    recovery={"status": "RECOVERED_PARTIAL", "report": "crash_recovery/report.json",
                              "interrupted_stage": report["interrupted_stage"],
                              "worker_exit_code": returncode, "last_saved_session_s": last})
    manifest["cleanup"] = {"completed": False, "order": [],
                           "errors": ["WORKER_DIED_BEFORE_CONFIRMED_CLEANUP"]}
    manifest["completeness"] = {"software_artifacts_complete": False,
                                "issues": ["ABNORMAL_PROCESS_EXIT", "PARTIAL_RECORDS_ONLY"]}
    atomic_json(manifest_path, manifest)
    if manifest.get("collection_version") == "P8_COLLECTION_V1":
        from src.p8.collection_report import finalize_collection

        finalize_collection(directory)
    return export_operator_results(directory, closed=True)
