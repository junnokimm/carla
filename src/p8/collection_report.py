"""Post-run log reductions. No CARLA calls, control changes, or input consumption."""

# ruff: noqa: BLE001 -- report failure must never change the experiment outcome
import csv
import json
from pathlib import Path

from src.p8.collection_clock import five_markers, map_time, tobii_alignment
from src.p8.collection_gaze import gaze_metrics
from src.p8.collection_hazard import hazard_metrics
from src.p8.collection_hazard_boundary import hazard_boundary
from src.p8.collection_io import notice, records, save_json
from src.p8.collection_surt import external_surt_reference
from src.p8.collection_time_axis import elapsed_ns, qpc_windows

VERSION = "P8_COLLECTION_V2_QPC"


def csv_rows(path, errors):
    old_limit = csv.field_size_limit(16 * 1024 * 1024)
    try:
        with path.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source, strict=True):
                if None in row or any(value is None for value in row.values()):
                    if len(errors) < 100:
                        errors.append(f"{path.name}:INCOMPLETE_RECORD")
                    continue
                yield row
    except (OSError, ValueError, UnicodeError, csv.Error) as error:
        errors.append(f"{path.name}:{error}")
    finally:
        csv.field_size_limit(old_limit)


def active_windows(events, phase, telemetry_end):
    windows, start = [], None
    label = f"{phase}_RUNNING"
    for row in events:
        if row["phase"] != phase:
            continue
        # Orchestrator preparation/calibration events share the module phase but
        # contain JSON. Only the driving runtime's literal boundaries count.
        value = row.get("event_value", "")
        t = float(row["session_elapsed_s"])
        if row["event_type"] == "stage_start" and value == label:
            start = t
        elif (
            row["event_type"] == "stage_end"
            and value.startswith(label + ":")
            and start is not None
        ):
            windows.append((start, t))
            start = None
    if start is not None and telemetry_end is not None and telemetry_end >= start:
        windows.append((start, telemetry_end))
    return windows


def exclude_pauses(windows, manifest):
    origin_s = manifest["origin"]["monotonic_ns"] / 1e9
    for fault in manifest.get("components", {}).get("runtime_faults", []):
        if "host_started_s" not in fault:
            continue
        start = fault["host_started_s"] - origin_s
        end = start + fault.get("paused_seconds", float("inf"))
        clipped = []
        for a, b in windows:
            if start >= b or end <= a:
                clipped.append((a, b))
            else:
                if a < start:
                    clipped.append((a, start))
                if end < b:
                    clipped.append((end, b))
        windows = clipped
    return windows


def _inverse(fit, session_s):
    if fit.get("status") != "SOFTWARE_ALIGNMENT_CHECKED":
        return None
    source = fit["source_anchor_s"] + (session_s - fit["target_anchor_s"]) / fit["rate"]
    return source if map_time(fit, source) is not None else None


def build_reports(directory, *, output_directory=None):
    """Call only after writers close. Re-running updates derived files, not sources."""
    directory = Path(directory)
    errors = []
    manifest = json.loads(
        (directory / "session_manifest.json").read_text(encoding="utf-8")
    )
    run_id = manifest["run_id"]
    origin_ns = manifest["origin"]["monotonic_ns"]
    origin_qpc = manifest["origin"].get("perf_counter_ns")
    output = (
        Path(output_directory)
        if output_directory is not None
        else directory / "collection"
    )
    output.mkdir(parents=True, exist_ok=True)
    event_path = directory / f"research_events_{run_id}.csv"
    # Large input batches are streamed separately; do not hold an entire drive's
    # multi-hundred-MB raw input history twice in memory just to inspect 5 seconds.
    events = [
        r
        for r in csv_rows(event_path, errors)
        if r.get("session_elapsed_s")
        and r.get("event_type")
        and r["event_type"] != "m2_hazard_raw_samples"
    ]
    events.sort(key=lambda r: float(r["session_elapsed_s"]))
    for row in events:
        row["analysis_elapsed_s"] = elapsed_ns(
            row.get("host_perf_counter_ns"), origin_qpc
        )
    decoded = []
    for row in events:
        if row["event_type"].startswith("m2_hazard_"):
            try:
                payload = json.loads(row["event_value"])
                if isinstance(payload, dict):
                    row["payload"] = payload
                    if "event_observed_perf_counter_ns" in payload:
                        row["analysis_elapsed_s"] = elapsed_ns(
                            payload["event_observed_perf_counter_ns"], origin_qpc
                        )
                    if (
                        row["event_type"] == "m2_hazard_display_candidate"
                        and origin_qpc is not None
                    ):
                        row["analysis_elapsed_s"] = elapsed_ns(
                            payload.get("flip_completed_perf_counter_ns"), origin_qpc
                        )
                    decoded.append(
                        (row["event_type"], float(row["session_elapsed_s"]), payload)
                    )
            except (ValueError, TypeError) as error:
                errors.append(f"event_decode:{error}")
    if events:
        with (output / "events_time_aligned.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(
                stream,
                [
                    "run_id",
                    "participant_id",
                    "module_1_condition",
                    "module_2_condition",
                    "condition",
                    "phase",
                    "event_type",
                    "session_elapsed_s",
                    "analysis_elapsed_s",
                    "host_perf_counter_ns",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(events)
    onset = next(
        (
            p.get("flip_completed_monotonic_ns")
            for n, _, p in decoded
            if n == "m2_hazard_display_candidate"
        ),
        None,
    )
    if onset is not None:
        for row in csv_rows(event_path, errors):
            if row.get("event_type") != "m2_hazard_raw_samples":
                continue
            try:
                payload = json.loads(row["event_value"])
                payload["samples"] = [
                    s
                    for s in payload.get("samples", [])
                    if onset - 250_000_000
                    <= s["poll_completed_monotonic_ns"]
                    <= onset + 5_250_000_000
                ]
                if payload["samples"]:
                    decoded.append(
                        (row["event_type"], float(row["session_elapsed_s"]), payload)
                    )
            except (KeyError, ValueError, TypeError) as error:
                errors.append(f"input_batch_decode:{error}")
    ends, anchors, previous_frame, last_anchor = {}, [], None, -float("inf")
    qpc_ends = {}
    qpc_coverage = {
        p: {"rows": 0, "missing_or_invalid_snapshot_qpc": 0}
        for p in ("MODULE_1", "MODULE_2")
    }
    known_windows = {
        p: exclude_pauses(active_windows(events, p, float("inf")), manifest)
        for p in ("MODULE_1", "MODULE_2")
    }
    states, state_index = {}, 0
    state_events = [
        r for r in events if r["event_type"] in ("initial_state", "auto_state")
    ]
    files, writers = {}, {}
    try:
        for row in csv_rows(directory / f"research_telemetry_{run_id}.csv", errors):
            phase = row.get("phase")
            if phase not in ("MODULE_1", "MODULE_2") or not row.get(
                "session_elapsed_s"
            ):
                continue
            t = float(row["session_elapsed_s"])
            ends[phase] = t
            row["record_observed_analysis_elapsed_s"] = elapsed_ns(
                row.get("host_perf_counter_ns"), origin_qpc
            )
            row["analysis_elapsed_s"] = elapsed_ns(
                row.get("carla_capture_completed_perf_counter_ns"), origin_qpc
            )
            capture_start = elapsed_ns(
                row.get("carla_capture_started_perf_counter_ns"), origin_qpc
            )
            capture_end = row["analysis_elapsed_s"]
            if (
                capture_start is None
                or capture_end is None
                or capture_end < capture_start
            ):
                row["analysis_elapsed_s"] = None
                qpc_coverage[phase]["missing_or_invalid_snapshot_qpc"] += 1
            qpc_coverage[phase]["rows"] += 1
            row["analysis_clock"] = "SHARED_QPC_SNAPSHOT_RETRIEVAL_NOT_SIMULATION_TICK"
            if row["analysis_elapsed_s"] is not None:
                qpc_ends[phase] = row["analysis_elapsed_s"]
            while (
                state_index < len(state_events)
                and float(state_events[state_index]["session_elapsed_s"]) <= t
            ):
                event = state_events[state_index]
                states[event["phase"]] = int(
                    event["event_value"].endswith("NOA_ACTIVE")
                )
                state_index += 1
            row["noa_state"] = states.get(phase, "")
            row["noa_state_basis"] = (
                "EVENT_RECONSTRUCTION" if phase in states else "UNKNOWN"
            )
            row["active_driving"] = int(
                any(a <= t < b for a, b in known_windows[phase])
            )
            row["module_elapsed_s"] = (
                t - known_windows[phase][0][0] if known_windows[phase] else ""
            )
            if phase not in writers:
                files[phase] = (output / f"{phase}_vehicle.csv").open(
                    "w", newline="", encoding="utf-8"
                )
                writers[phase] = csv.DictWriter(files[phase], list(row))
                writers[phase].writeheader()
            writers[phase].writerow(row)
            if (
                row.get("carla_simulation_seconds")
                and row.get("carla_frame")
                and t - last_anchor >= 1
            ):
                frame = int(row["carla_frame"])
                if frame != previous_frame:
                    anchors.append(
                        {
                            "session_elapsed_s": t,
                            "analysis_elapsed_s": row["analysis_elapsed_s"],
                            "capture_started_perf_counter_ns": row.get(
                                "carla_capture_started_perf_counter_ns"
                            ),
                            "capture_completed_perf_counter_ns": row.get(
                                "carla_capture_completed_perf_counter_ns"
                            ),
                            "carla_frame": frame,
                            "carla_simulation_seconds": float(
                                row["carla_simulation_seconds"]
                            ),
                            "capture_started_ns": row.get(
                                "carla_capture_started_host_monotonic_ns"
                            ),
                            "capture_completed_ns": row.get(
                                "carla_capture_completed_host_monotonic_ns"
                            ),
                        }
                    )
                    previous_frame, last_anchor = frame, t
    finally:
        for stream in files.values():
            stream.close()
    windows = {
        p: exclude_pauses(active_windows(events, p, ends.get(p)), manifest)
        for p in ("MODULE_1", "MODULE_2")
    }
    boundary = hazard_boundary(events, qpc=origin_qpc is not None, origin_ns=origin_ns)
    hazard_source_errors = list(errors)
    analysis_windows, alignment_errors = {}, {}
    for phase in ("MODULE_1", "MODULE_2"):
        analysis_windows[phase], alignment_errors[phase] = (
            qpc_windows(events, phase, qpc_ends.get(phase), manifest)
            if origin_qpc is not None
            else ([], ["LEGACY_NO_QPC_ORIGIN"])
        )
    clocks, gaze_reports = {}, []
    folders = list((directory / "gaze").glob("*"))
    # A supervisor copies pending files only after confirming worker death.
    # Never read a still-running gaze writer's original pending directory.
    if manifest.get("recovery", {}).get("status") == "RECOVERED_PARTIAL":
        folders += list(
            (directory / "crash_recovery" / "pending_gaze" / "gaze_pending").glob("*")
        )
    for folder in sorted(folders):
        if not folder.is_dir():
            continue
        try:
            alignment = tobii_alignment(
                records(folder / "raw_gaze.jsonl", errors),
                origin_ns,
                origin_perf_counter_ns=origin_qpc,
            )
            clocks[folder.name] = alignment
            if not folder.name.startswith("MODULE_2_RUNNING"):
                continue
            if (folder / "summary.json").exists():
                summary = json.loads(
                    (folder / "summary.json").read_text(encoding="utf-8")
                )
            else:
                metadata = next(
                    (
                        r
                        for r in records(folder / "gaze.jsonl", errors)
                        if r.get("record_type") == "metadata"
                    ),
                    {},
                )
                summary = {
                    "status": "RECOVERED_PARTIAL",
                    "gaze_precheck": metadata.get("gaze_precheck", {}),
                }
            eligible = summary.get("gaze_precheck", {}).get("eligible_aois", {})
            report, aligned = gaze_metrics(
                records(folder / "gaze.jsonl", errors),
                windows=analysis_windows["MODULE_2"]
                if origin_qpc is not None
                else windows["MODULE_2"],
                alignment=alignment,
                eligible=eligible,
                hazard_start=boundary["start_s"],
                hazard_boundary=boundary,
            )
            report.update(
                source=str(folder.relative_to(directory)),
                capture_status=summary.get("status"),
                source_errors=summary.get("errors", []),
                clock_status=alignment["status"],
            )
            report["boundary_alignment_errors"] = alignment_errors["MODULE_2"]
            if manifest.get("logging_health", {}).get("failure_count") or (
                origin_qpc is not None and alignment_errors["MODULE_2"]
            ):
                for cohort in report["cohorts"].values():
                    cohort["analysis_sample_ratio_by_aoi"] = dict.fromkeys(
                        cohort["analysis_sample_ratio_by_aoi"]
                    )
                    cohort["analysis_observed_seconds_by_aoi"] = dict.fromkeys(
                        cohort["analysis_observed_seconds_by_aoi"]
                    )
            gaze_reports.append(report)
            with (output / f"{folder.name}_aligned_gaze.jsonl").open(
                "w", encoding="utf-8"
            ) as stream:
                for row in aligned:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
        except Exception as error:
            errors.append(f"gaze:{folder.name}:{type(error).__name__}:{error}")
    surt = external_surt_reference(manifest, windows["MODULE_1"])
    save_json(output / "surt_external_link.json", surt)
    markers = five_markers(anchors)
    for marker in markers:
        t = marker["session_elapsed_s"]
        marker["tobii_system_us_by_capture"] = {}
        marker["tablet_clock_ms"] = {}
        for name, fit in clocks.items():
            if fit.get("session_clock") == "SHARED_QPC":
                qpc = (
                    None
                    if marker["analysis_elapsed_s"] is None
                    else marker["analysis_elapsed_s"] + origin_qpc / 1e9
                )
            else:
                qpc = _inverse(fit["shared_qpc_to_session"], t)
            sdk = None if qpc is None else _inverse(fit["sdk_to_shared_qpc"], qpc)
            if sdk is not None:
                marker["tobii_system_us_by_capture"][name] = sdk * 1e6
        marker["all_three_clock_domains_available"] = bool(
            marker["tobii_system_us_by_capture"] and marker["tablet_clock_ms"]
        )
    save_json(
        output / "clock_sync.json",
        {
            "version": VERSION,
            "analysis_clock": "SHARED_QPC"
            if origin_qpc is not None
            else "LEGACY_PARENT_MONOTONIC",
            "origin_perf_counter_ns": origin_qpc,
            "vehicle_observation_qpc_coverage": qpc_coverage,
            "analysis_active_windows": analysis_windows,
            "boundary_alignment_errors": alignment_errors,
            "control_clock_policy": "UNCHANGED_MONOTONIC;QPC_IS_ANALYSIS_ONLY",
            "tobii": clocks,
            "tablet": {"status": surt["tablet_clock_alignment"]},
            "markers": markers,
            "three_clock_software_check": len(markers) == 5
            and all(m["all_three_clock_domains_available"] for m in markers),
            "physical_sync_validated": False,
            "marker_basis": "FIVE_RETAINED_SIMULATOR_ANCHORS_WITH_BOUNDED_CLOCK_ESTIMATES",
            "simulator_mapping": "SNAPSHOT_FRAME_AND_CAPTURE_BRACKETS;SIMULATION_RATE_IS_NOT_CLOCK_DRIFT",
            "simulator_anchors": anchors,
        },
    )
    save_json(
        output / "m2_gaze_metrics.json",
        {
            "version": VERSION,
            "captures": gaze_reports,
            "status": "COLLECTED" if gaze_reports else "NO_COMPLETED_M2_GAZE_CAPTURE",
            "pending_captures_excluded": [
                p.name for p in (directory / ".gaze_pending").glob("*")
            ],
            "notes": "RATIOS_ARE_SAMPLES;SECONDS_ARE_ADJACENT_OBSERVED_INTERVALS;MISSING_IS_NOT_TABLET",
        },
    )
    try:
        hazard = hazard_metrics(decoded, origin_ns)
        hazard["qpc_alignment"] = (
            hazard_metrics(decoded, origin_qpc, precise=True)
            if origin_qpc is not None
            else {"status": "UNAVAILABLE_LEGACY_QPC_NOT_RECORDED"}
        )
        if hazard_source_errors or manifest.get("logging_health", {}).get(
            "failure_count"
        ):
            hazard["analysis_reaction_time_s"] = None
            hazard["source_incomplete"] = True
            hazard["qpc_alignment"]["analysis_reaction_time_s"] = None
            hazard["qpc_alignment"]["source_incomplete"] = True
        save_json(output / "hazard_response_metrics.json", hazard)
    except Exception as error:
        errors.append(f"hazard_reduction:{type(error).__name__}:{error}")
        save_json(
            output / "hazard_response_metrics.json",
            {"status": "REDUCTION_FAILED", "error": str(error)},
        )
    health = manifest.get("logging_health", {})
    save_json(
        output / "collection_status.json",
        {
            "version": VERSION,
            "run_id": run_id,
            "participant_id": manifest["participant_id"],
            "source_session_state": manifest.get("state"),
            "source_completion_status": manifest.get("completion_status"),
            **({"execution_scope": "M2_ONLY_TEST", "m1_performed": False}
               if manifest.get("execution_scope") == "M2_ONLY_TEST" else {}),
            "active_windows": windows,
            "status": "PARTIAL"
            if errors or health.get("failure_count")
            else "REPORTS_WRITTEN",
            "errors": errors,
            "logging_health": health,
            "surt_status": surt["status"],
            "m1_condition": surt["m1_condition"],
            "m1_surt_assigned": surt["m1_surt_assigned"],
            "surt_actual_app_execution_verified": False,
            "experiment_action": "UNCHANGED",
            "physical_steering_angle": "NOT_COLLECTED_NORMALIZED_STEERING_ONLY",
            "eligibility": "REPORTS_WRITTEN_DOES_NOT_MEAN_ALL_HARDWARE_VALIDATED",
        },
    )


def finalize_collection(directory):
    try:
        build_reports(directory)
    except Exception as error:
        notice(f"postrun_reduction:{type(error).__name__}:{error}")
        try:
            save_json(
                Path(directory) / "collection" / "collection_status.json",
                {
                    "status": "FAILED",
                    "error": str(error),
                    "experiment_action": "UNCHANGED",
                },
            )
        except Exception as persistence_error:
            notice(f"collection_status_unwritable:{persistence_error}")
