"""Nonfatal, read-only gaze sidecar. No SDK, disk I/O or waits in the frame hook."""
# ruff: noqa: BLE001 -- optional observation failures must not terminate driving

import json
import threading
import time
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from src.p6.external_tablet import ExternalTablet, tracked_eyes
from src.p6.gaze_duration import GazeDuration
from src.p6.gaze_precheck import AOIS, analysis_seconds, stage_quality
from src.p6.models import Validity
from src.p6.pilot_aoi import classify_screen, layout_for_view, layout_record
from src.p6.pilot_mapping_evidence import aoi_schema, mapping_evidence
from src.p6.tobii.client import TobiiProcessSource
from src.p6.validation.mapping import WindowSpansMonitors
from src.p6.validation.windows import WindowProbe
from src.p8.pilot_display import mapping_issue, mapping_signature


def notice(status, **details):
    try:
        print(json.dumps({"status": status, "session_action": "CONTINUE", **details}), flush=True)
    except Exception:  # noqa: S110 -- even a broken console must remain nonfatal
        pass


class PilotGaze:
    def __init__(self, services, stage, view_config, config_path,
                 *, source_factory=TobiiProcessSource, probe_factory=WindowProbe):
        self.config_path = Path(config_path)
        self.directory = services.directory
        self.run_id = str(services.context.study_run_id)
        self.participant_id = str(services.context.participant_id)
        self.study_conditions = {
            key: getattr(services.context, key, None)
            for key in ("module_1_condition", "module_2_condition")
        }
        self.origin_ns = services.clock.origin.monotonic_ns
        self.origin_perf_ns = getattr(services.clock.origin, "perf_counter_ns", None)
        self.stage = stage.value
        self.condition = next(
            (value for key, value in self.study_conditions.items()
             if self.stage.startswith(key.removesuffix("_condition").upper() + "_")),
            None,
        )
        module = "m1" if self.stage == "MODULE_1_RUNNING" else "m2"
        self.gaze_quality = deepcopy(stage_quality(self.stage, getattr(services, f"{module}_gaze_quality", None)))
        self.expected_mapping = self.gaze_quality.get("display_mapping")
        self.require_fullscreen = self.expected_mapping is not None
        self.mapping_checked = False
        self.layout = layout_for_view(view_config)
        self.source_factory, self.probe_factory = source_factory, probe_factory
        self.stop = threading.Event()
        self.presentation = None
        self.last_present_ns = 0
        self._next_publish = 0
        self._display_size = None
        self.thread = None
        self.source = None
        self.focused = False
        self.geometry = None
        self.geometry_ns = 0
        self.geometry_key = None
        self.geometry_issue = None
        self.mapping_id = None
        self.evidence_geometry = None
        self.monitor = None
        if not hasattr(services, "pilot_gaze_monitor"):
            services.pilot_gaze_monitor = {}
        self.monitor_binding = services.pilot_gaze_monitor
        self.last_sample_ns = None
        self.last_sequence = None
        self.counts = Counter()
        self.errors = []
        self.pending = self.directory / ".gaze_pending" / f"{self.stage}-{uuid4().hex[:8]}"
        self.config = {}
        self.wire = self.gaze = None
        self.intervals = None
        self.duration = None
        self.tablet = None
        self.tablet_binding = "TABLET_PROFILE_NOT_CONFIGURED"
        self.started_ns = None
        self.closed_ns = None
        self.started_perf_ns = None
        self.closed_perf_ns = None

    def start(self):
        self.started_ns = time.monotonic_ns()
        self.started_perf_ns = time.perf_counter_ns()
        self.thread = threading.Thread(target=self._run, name="pilot-gaze", daemon=True)
        self.thread.start()

    def present(self, display_size):
        # Called only by the renderer, never reads/consumes keyboard or wheel events.
        if self.closed_ns is not None:
            return
        now = time.monotonic_ns()
        self.last_present_ns = now
        if now < self._next_publish and display_size == self._display_size:
            return
        self._next_publish = now + 250_000_000
        self._display_size = display_size
        try:
            import pygame
            hwnd = pygame.display.get_wm_info().get("window")
            self.presentation = (now, hwnd, display_size)
        except Exception as error:
            self.presentation = None
            self._error("PRESENTATION", error)

    def suspend_presentation(self):
        # Called before SDL can replace a window. Queued samples remain raw,
        # but cannot inherit coordinates from a different window generation.
        self.presentation = None
        self._next_publish = 0
        self.geometry_key = None

    def end_presentation(self):
        # No disk I/O or wait here. Close the observation window BEFORE SDL quit.
        if self.closed_ns is None:
            self.closed_ns = time.monotonic_ns()
            self.closed_perf_ns = time.perf_counter_ns()
        self.suspend_presentation()
        self.stop.set()

    def _error(self, phase, error):
        detail = f"{phase}:{type(error).__name__}:{error}"
        if detail not in self.errors and len(self.errors) < 32:
            self.errors.append(detail)
        if self.stage in ("MODULE_1_RUNNING", "MODULE_2_RUNNING"):
            if phase in ("COLLECTION", "WORKER", "DURATION", "MAPPING", "MAPPING_VALIDATION"):
                self._exclude_aois(AOIS, detail)
            elif phase == "TABLET_PROFILE":
                self._exclude_aois(("TABLET",), detail)

    def _exclude_aois(self, aois, reason):
        for aoi in aois:
            self.gaze_quality["eligible_aois"][aoi] = False
        self.gaze_quality["excluded_aois"] = sorted(set(self.gaze_quality["excluded_aois"]) | set(aois))
        reasons = self.gaze_quality.setdefault("runtime_exclusion_reasons", [])
        if reason not in reasons:
            reasons.append(reason)

    def _record(self, handle, record):
        handle.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")

    def _raw(self, line):
        self.wire.write(line if line.endswith("\n") else line + "\n")
        if '"clock_pair"' in line:
            # Python 3.10 worker monotonic and Python 3.12 parent monotonic may
            # have different Windows origins. Bridge through the shared QPC.
            before = time.monotonic_ns()
            perf = time.perf_counter_ns()
            after = time.monotonic_ns()
            self._record(self.wire, {"record_type": "parent_clock_bridge",
                "host_before_ns": before, "host_after_ns": after, "perf_counter_ns": perf})
        if self.tablet is not None and '"display_area"' in line:
            record = json.loads(line)
            if record.get("record_type") == "display_area":
                self.tablet_binding = ("DISPLAY_BINDING_MATCHED_AT_CAPTURE_START"
                                       if self.tablet.display_matches(record)
                                       else "TABLET_DISPLAY_BINDING_MISMATCH_OR_UNAVAILABLE")
                if self.stage in ("MODULE_1_RUNNING", "MODULE_2_RUNNING") and self.tablet_binding != "DISPLAY_BINDING_MATCHED_AT_CAPTURE_START":
                    self._exclude_aois(("TABLET",), self.tablet_binding)
                notice("TABLET_MAPPING_STATUS", stage=self.stage, binding=self.tablet_binding,
                       mapping_status=self.tablet.profile["status"])

    def _load_tablet(self):
        path = self.config.get("tablet_profile")
        if path:
            try:
                self.tablet = ExternalTablet.load(self.config_path.parent / path)
                self.tablet_binding = "TABLET_DISPLAY_BINDING_NOT_RECEIVED"
                self._record(self.gaze, {"record_type": "tablet_profile", "profile": self.tablet.profile,
                                         "sha256": self.tablet.sha256})
                checked_hash = self.gaze_quality.get("tablet_profile_sha256")
                if self.stage in ("MODULE_1_RUNNING", "MODULE_2_RUNNING") and checked_hash != self.tablet.sha256:
                    self.gaze_quality["eligible_aois"]["TABLET"] = False
                    self.gaze_quality["excluded_aois"] = sorted(set(self.gaze_quality["excluded_aois"]) | {"TABLET"})
                    self.gaze_quality["tablet_profile_changed_since_precheck"] = True
            except Exception as error:
                self.tablet = None
                self.tablet_binding = "TABLET_PROFILE_FAILED"
                self._error("TABLET_PROFILE", error)
                notice("TABLET_MAPPING_FAILED_CONTINUE", reason=str(error))

    def _tablet_status(self):
        return self.tablet.profile["status"] if self.tablet else self.tablet_binding

    def _interval(self, row):
        # Separate clock origins: align only for approximate session-relative labels.
        session_offset = self.started_ns - self.origin_ns - self.started_perf_ns
        aoi = "TABLET" if row["state"].startswith("TABLET_") else row["state"]
        self._record(self.intervals, {**row, "run_id": self.run_id, "stage": self.stage,
                                      "analysis_aoi_included": self.gaze_quality["eligible_aois"].get(aoi),
                                      "session_start_s": (row["start_clock_ns"] + session_offset) / 1e9,
                                      "session_end_s": (row["end_clock_ns"] + session_offset) / 1e9,
                                      "session_alignment": "START_HOST_CLOCK_PAIR_APPROXIMATE"})

    def _mapping(self, callback_ns):
        if self.closed_ns is not None:
            return "PRESENTATION_ENDED"
        presentation = self.presentation
        now = time.monotonic_ns()
        if presentation is None or now - self.last_present_ns > 1_000_000_000:
            return "NO_ACTIVE_PRESENTATION"
        published, hwnd, size = presentation
        if not hwnd:
            return "WINDOW_HANDLE_UNAVAILABLE"
        # Do not label a delayed sample with a newer window configuration.
        if callback_ns < published or now - callback_ns > 1_000_000_000:
            return "PRESENTATION_TIME_UNCERTAIN"
        key = (hwnd, size)
        if key != self.geometry_key or now - self.geometry_ns >= 250_000_000:
            probe = self.probe_factory()
            try:
                geometry = probe.snapshot(hwnd, size)
                focused = probe.focused(hwnd)
            except WindowSpansMonitors:
                # Dragging across monitors cannot be mapped to either display.
                # Keep raw samples, retry on the usual cadence, and recover once
                # the operator finishes moving the window. Do not reuse geometry.
                self.geometry, self.focused = None, False
                self.geometry_ns, self.geometry_key = now, key
                self.geometry_issue = "WINDOW_SPANS_MONITORS"
                return self.geometry_issue
            except Exception:
                # A deliberate lifecycle change during this probe is not a
                # failed active-window mapping. Never reuse its cached geometry.
                if self.closed_ns is not None or self.presentation is not presentation:
                    return "PRESENTATION_ENDED" if self.closed_ns is not None else "PRESENTATION_CHANGED"
                raise
            finally:
                probe.close()
            if self.closed_ns is not None or self.presentation is not presentation:
                return "PRESENTATION_ENDED" if self.closed_ns is not None else "PRESENTATION_CHANGED"
            self.geometry, self.focused = geometry, focused
            self.geometry_ns = now
            self.geometry_key = key
            self.geometry_issue = None
            record = {"record_type": "mapping", "host_monotonic_ns": now,
                      "geometry": asdict(self.geometry)}
            if self.geometry != self.evidence_geometry:
                mapping_id, definition = mapping_evidence(self.geometry, self.layout)
                record.update(mapping_id=mapping_id, definition=definition)
                self._record(self.gaze, record)
                self.mapping_id, self.evidence_geometry = mapping_id, self.geometry
            else:
                self._record(self.gaze, record | {"mapping_id": self.mapping_id})
        if self.geometry_issue:
            return self.geometry_issue
        if not self.focused:
            return "WINDOW_NOT_FOCUSED"
        if self.require_fullscreen and mapping_issue(self.geometry) == "FULLSCREEN_MAPPING_REQUIRED":
            # Windowed startup / manual F11 transitions are temporarily unmapped.
            # Resume classification only after the checked fullscreen mapping returns.
            return "FULLSCREEN_MAPPING_REQUIRED"
        if self.monitor is None:
            checked_monitor = (self.expected_mapping or {}).get("monitor_name")
            # A precheck is the operator's explicit selection of the driving display.
            precheck_monitor = (self.geometry.monitor_name if self.require_fullscreen and
                                self.stage in ("MODULE_1_PREPARE", "MODULE_2_PREPARE") else None)
            self.monitor = (self.config.get("monitor_name") or checked_monitor or precheck_monitor
                            or self.monitor_binding.get("name")
                            or self.geometry.monitor_name)
            self.monitor_binding["name"] = self.monitor
        if self.geometry.monitor_name != self.monitor:
            return "TRACKER_MONITOR_MISMATCH"
        if not self.config.get("display_confirmed", False):
            return "DISPLAY_MAPPING_NOT_CONFIRMED"
        if self.require_fullscreen:
            issue = mapping_issue(self.geometry, self.expected_mapping)
            if issue:
                self._error("MAPPING_VALIDATION", RuntimeError(issue))
                return issue
            self.mapping_checked = True
        return None

    def _sample(self, sample):
        now = time.monotonic_ns()
        self.last_sample_ns = now
        callback_ns = sample.source_metadata["sdk_callback_host_monotonic_ns"]
        callback_perf_ns = sample.source_metadata.get("sdk_callback_host_perf_counter_ns")
        parent_callback_ns = (None if callback_perf_ns is None or self.started_perf_ns is None
                              else self.started_ns + callback_perf_ns - self.started_perf_ns)
        duration_eligible = (callback_perf_ns is not None
                             and (self.started_perf_ns is None or callback_perf_ns >= self.started_perf_ns)
                             and (self.closed_perf_ns is None or callback_perf_ns <= self.closed_perf_ns))
        reason = None
        try:
            reason = self._mapping(parent_callback_ns if parent_callback_ns is not None else callback_ns)
        except Exception as error:
            reason = "MAPPING_UNAVAILABLE"
            # Retry the entire observation/evidence write; do not classify using
            # new geometry whose mapping definition failed to reach the log.
            self.geometry_key = None
            self._error("MAPPING", error)
        if reason is not None:
            self.counts[f"mapping_reason:{reason}"] += 1
        eyes = [eye for eye in (sample.left, sample.right) if eye.validity is Validity.VALID]
        screen_gaze = ({"x": sum(e.x for e in eyes) / len(eyes),
                        "y": sum(e.y for e in eyes) / len(eyes)} if eyes else None)
        tracked = tracked_eyes(sample)
        mapping_reason = reason
        aoi, point = "NO_AOI", None
        tablet_hits = {}
        if not tracked:
            reason = "NO_VALID_EYE"
            state = "TRACKING_LOST"
        elif reason is not None:
            state = "MAPPING_UNAVAILABLE"
        else:
            state = "VALID_UNMAPPED"
        if eyes and reason is None:
            aoi, point = classify_screen(screen_gaze["x"], screen_gaze["y"],
                                         self.geometry, self.layout)
            state = aoi if aoi != "NO_AOI" else "VALID_UNMAPPED"
        if tracked and mapping_reason is None and self.tablet is not None:
            if self.tablet_binding == "DISPLAY_BINDING_MATCHED_AT_CAPTURE_START":
                hit, tablet_reason, tablet_hits = self.tablet.classify(sample)
                if hit and aoi == "NO_AOI":
                    aoi, reason = "TABLET", tablet_reason
                    state = "TABLET_MONOCULAR" if len(tracked) == 1 else "TABLET_BINOCULAR"
                elif hit or tablet_reason == "TABLET_EYE_DISAGREEMENT":
                    aoi, reason, state = "NO_AOI", "TABLET_SCREEN_CONFLICT" if hit else tablet_reason, "AOI_UNCERTAIN"
                elif aoi == "NO_AOI":
                    reason = tablet_reason
            elif aoi == "NO_AOI":
                reason = self.tablet_binding
        self.counts["samples"] += 1
        self.counts["valid_eye_samples"] += bool(eyes)
        self.counts["sdk_tracked_samples_including_off_display"] += bool(tracked)
        self.counts[aoi] += 1
        gap = 0 if self.last_sequence is None else max(0, sample.source_sequence - self.last_sequence - 1)
        self.counts["sequence_gaps"] += gap
        self.last_sequence = sample.source_sequence
        self._record(self.gaze, {
            "record_type": "gaze", "run_id": self.run_id, "participant_id": self.participant_id,
            "stage": self.stage, "condition": self.condition,
            "synthetic": False, "sample": asdict(sample),
            "sdk_callback_host_monotonic_ns": callback_ns, "ipc_receive_monotonic_ns": now,
            "duration_callback_perf_counter_ns": callback_perf_ns,
            "duration_eligible": duration_eligible,
            "session_elapsed_s": (callback_ns - self.origin_ns) / 1e9,
            "parent_callback_session_s": None if parent_callback_ns is None else (parent_callback_ns-self.origin_ns)/1e9,
            "session_time_basis": "SDK_CALLBACK_HOST_NOT_DEVICE_ACQUISITION",
            "clock_sync_validated": False, "sequence_gap": gap,
            "aoi": aoi, "aoi_reason": reason, "render_point": point,
            "mapping_id": self.mapping_id if mapping_reason is None else None,
            "mapping_reason": mapping_reason,
            "screen_gaze_display_normalized": screen_gaze,
            "tablet_profile_sha256": getattr(self.tablet, "sha256", None),
            "aoi_version": self.layout.version, "aoi_validation_status": "NOT_VALIDATED",
            "fusion_policy": "SCREEN_MEAN_DISPLAY_VALID_EYES;TABLET_PER_EYE_AGREEMENT",
            "mapping_observed_monotonic_ns": self.geometry_ns,
            "calibration_status": self.config.get("calibration_status", "NOT_VERIFIED"),
            "tablet_status": self._tablet_status(), "tablet_binding": self.tablet_binding,
            "tablet_eye_hits": tablet_hits, "sdk_tracked_eyes": list(tracked),
            "observation_state": state,
            "analysis_aoi_included": self.gaze_quality["eligible_aois"].get(aoi),
        })
        if self.duration is not None:
            if callback_perf_ns is None:
                self.counts["duration_samples_without_perf_clock"] += 1
            elif duration_eligible:
                self.duration.add(callback_perf_ns, sample.source_sequence,
                                  sample.source_metadata["device_timestamp_raw"], state)
            else:
                self.counts["duration_samples_outside_stage_window"] += 1
        if self.counts["samples"] == 1:
            notice("GAZE_RECEIVING", stage=self.stage, aoi_validation="NOT_VALIDATED")
        if self.counts["samples"] % 60 == 0:
            self.wire.flush()
            self.gaze.flush()
            if self.intervals is not None:
                self.intervals.flush()

    def _run(self):
        try:
            self.pending.mkdir(parents=True, exist_ok=False)
            if self.started_ns is None:
                self.started_ns, self.started_perf_ns = time.monotonic_ns(), time.perf_counter_ns()
            self.duration = GazeDuration(self.started_perf_ns, self._interval)
            self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            if not self.config.get("enabled", True):
                return
            with (self.pending / "raw_gaze.jsonl").open("x", encoding="utf-8") as wire, \
                 (self.pending / "gaze.jsonl").open("x", encoding="utf-8") as gaze, \
                 (self.pending / "intervals.jsonl").open("x", encoding="utf-8") as intervals:
                self.wire, self.gaze, self.intervals = wire, gaze, intervals
                self._load_tablet()
                self._record(gaze, {"record_type": "metadata", "run_id": self.run_id,
                                   "condition": self.condition, "study_conditions": self.study_conditions,
                                   "stage": self.stage, "session_origin_monotonic_ns": self.origin_ns,
                                   "session_origin_perf_counter_ns": self.origin_perf_ns,
                                   "duration_start_host_monotonic_ns": self.started_ns,
                                   "duration_start_perf_counter_ns": self.started_perf_ns,
                                   "layout": layout_record(self.layout), "config": self.config,
                                   "aoi_schema": aoi_schema(),
                                   "gaze_precheck": self.gaze_quality,
                                   "error_policy": "RECORD_AND_CONTINUE"})
                self.source = self.source_factory(Path(self.config["tobii_python"]).resolve(),
                                                  seconds=7200, pilot=True)
                devices = self.source.discover()
                serial = self.config.get("serial")
                if serial is None:
                    if len(devices) != 1:
                        raise RuntimeError("EXACTLY_ONE_TRACKER_OR_EXPLICIT_SERIAL_REQUIRED")
                    serial = devices[0].identifier
                self.source.select(serial)
                if self.stop.is_set():
                    return
                self.source.record_sink = self._raw
                self.source.subscribe(self._sample)
                notice("GAZE_STARTED", stage=self.stage, serial=serial)
                subscribed_at = time.monotonic_ns()
                try:
                    while not self.stop.wait(0.1):
                        if self.source.finished.is_set():
                            raise RuntimeError(f"WORKER_STOPPED:{self.source.last_error}")
                        if time.monotonic_ns() - (self.last_sample_ns or subscribed_at) > 5_000_000_000:
                            raise RuntimeError("NO_GAZE_CALLBACK_FOR_5_SECONDS")
                finally:
                    self.source.unsubscribe()
                    # Own the files until the receiver has actually stopped. A stalled
                    # writer stays hidden from ZIP export; the driving thread never waits indefinitely.
                    while self.source.running:
                        time.sleep(0.05)
                    if self.source.last_error not in (None, "RAW_CAPTURED", "STOPPED"):
                        self._error("WORKER", RuntimeError(self.source.last_error))
        except Exception as error:
            self._error("COLLECTION", error)
            notice("GAZE_FAILED_CONTINUE", stage=self.stage, reason=str(error))
        finally:
            self._finish_duration(self.closed_perf_ns or time.perf_counter_ns())
            self._summary()

    def _finish_duration(self, end_ns):
        if self.duration is None or not self.pending.exists():
            return
        try:
            with (self.pending / "intervals.jsonl").open("a", encoding="utf-8") as intervals:
                self.intervals = intervals
                self.duration.finish(end_ns)
        except Exception as error:
            self._error("DURATION", error)

    def _summary(self):
        try:
            (self.pending / "summary.json").write_text(json.dumps({
                "run_id": self.run_id, "stage": self.stage, "counts": dict(self.counts),
                "condition": self.condition, "study_conditions": self.study_conditions,
                "errors": self.errors, "session_action": "CONTINUE",
                "status": "FAILED" if self.errors else "COLLECTED" if self.counts["samples"] else "NO_SAMPLES",
                "aoi_validated": bool(self.gaze_quality.get("passed") and self.mapping_checked
                                      and all(self.gaze_quality["eligible_aois"].values())),
                "clock_sync_validated": False,
                "display_mapping_checked": self.mapping_checked,
                "expected_display_mapping": self.expected_mapping,
                "last_display_mapping": mapping_signature(self.geometry) if self.geometry else None,
                "last_mapping_id": self.mapping_id,
                "aoi_schema_version": "pilot-aoi-evidence-v1",
                "aoi_evidence": "gaze.jsonl: metadata.aoi_schema; mapping.definition; tablet_profile",
                "tablet_status": self._tablet_status(), "tablet_binding": self.tablet_binding,
                "tablet_profile_sha256": self.tablet.sha256 if self.tablet else None,
                "tablet_unobserved_targets": self.tablet.profile.get("missing_targets", []) if self.tablet else [],
                "duration": self.duration.summary() if self.duration else None,
                "gaze_precheck": self.gaze_quality,
                "analysis_seconds_by_aoi": analysis_seconds(self.duration.summary(), self.gaze_quality)
                if self.duration else dict.fromkeys(AOIS),
                "observation_window_closed": self.closed_ns is not None,
                "mapping_lifecycle_policy": "END_BEFORE_SDL_QUIT;TRANSITION_SAMPLES_UNMAPPED;REAL_ERRORS_RETAINED",
            }, indent=2), encoding="utf-8")
        except Exception as error:
            notice("GAZE_SUMMARY_FAILED_CONTINUE", reason=str(error))

    def close(self):
        self.end_presentation()
        if self.thread is not None:
            self.thread.join(timeout=1.5)
            if self.thread.is_alive():
                notice("GAZE_SHUTDOWN_PENDING", partial_directory=str(self.pending))
                return
        if self.pending.exists():
            # The worker may have failed earlier: retain the entire remaining stage
            # as NO_DATA, without extending its last known AOI.
            self._finish_duration(self.closed_perf_ns)
            self._summary()
            target = self.directory / "gaze" / self.pending.name
            target.parent.mkdir(exist_ok=True)
            self.pending.rename(target)
            try:
                self._totals()
            except Exception as error:
                notice("GAZE_TOTALS_FAILED_CONTINUE", reason=str(error))
            notice("GAZE_SAVED", directory=str(target), errors=self.errors)

    def _totals(self):
        stages, seconds, unavailable = [], Counter(), []
        for path in sorted((self.directory / "gaze").glob("*/summary.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            duration = report.get("duration")
            if duration is None:
                unavailable.append(str(path.relative_to(self.directory)))
                continue
            seconds.update(duration["seconds_by_state"])
            stages.append({"stage": report["stage"], "directory": path.parent.name,
                           "status": report["status"], "tablet_status": report["tablet_status"],
                           "gaze_precheck": report.get("gaze_precheck"),
                           "analysis_seconds_by_aoi": report.get("analysis_seconds_by_aoi", dict.fromkeys(AOIS)),
                           "tablet_binding": report["tablet_binding"], **duration})
        report = {"run_id": self.run_id, "scope": "COMPLETED_GAZE_SIDECARS_ONLY_NOT_REST_OR_WHOLE_SESSION",
                  "stages": stages, "summaries_without_duration": unavailable,
                  "seconds_by_state": dict(seconds),
                  "tablet_classified_seconds": seconds["TABLET_BINOCULAR"] + seconds["TABLET_MONOCULAR"],
                  "tracking_lost_seconds": seconds["TRACKING_LOST"], "no_data_seconds": seconds["NO_DATA"],
                  "true_total_tablet_seconds": None, "aoi_independently_validated": False}
        m2_stages = [s for s in stages if s["stage"] == "MODULE_2_RUNNING"]
        report["m2_analysis_seconds_by_aoi"] = {
            a: sum(s["analysis_seconds_by_aoi"][a] for s in m2_stages)
            if m2_stages and all(s["analysis_seconds_by_aoi"].get(a) is not None for s in m2_stages)
            else None for a in AOIS}
        report["analysis_rule"] = "M2_FAILED_OR_UNCHECKED_AOI_IS_NULL_NOT_ZERO; RAW_OBSERVED_TOTALS_RETAINED"
        temporary = self.directory / ".gaze_totals.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "gaze_totals.json")


class PilotGazeScope:
    """Never rewrite a driving exception, including frozen dataclass exceptions."""

    def __init__(self, viewer, services, stage, config_path):
        self.viewer, self.services, self.stage, self.config_path = viewer, services, stage, config_path
        self.collector = None

    def __enter__(self):
        try:
            if self.config_path is not None:
                self.collector = PilotGaze(self.services, self.stage, self.viewer._config, self.config_path)
                self.collector.start()
                self.viewer.gaze_observer = self.collector.present
                self.viewer.gaze_end_observer = self.collector.end_presentation
                self.viewer.gaze_transition_observer = self.collector.suspend_presentation
        except Exception as error:
            notice("GAZE_START_FAILED_CONTINUE", reason=str(error))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.collector is not None:
            try:
                self.viewer.gaze_observer = None
                self.viewer.gaze_end_observer = None
                self.viewer.gaze_transition_observer = None
                self.collector.close()
            except Exception as error:
                notice("GAZE_CLOSE_FAILED_CONTINUE", reason=str(error))
        return False


def collect_pilot_gaze(viewer, services, stage, config_path):
    return PilotGazeScope(viewer, services, stage, config_path)
