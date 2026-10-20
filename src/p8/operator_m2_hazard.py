"""M2 opportunity with an explicit confirmation boundary for obstacle contact."""

import json
from dataclasses import asdict
from random import Random
from uuid import uuid4

from src.error_notes import append_error_note
from src.experiment.observation_clock import observation_ns
from src.p7.metrics import canonical_hazard_record
from src.p7.moving_backend import MovingHazardBackend
from src.p7.moving_config import SCENARIO_VERSION, TARGET_TTC_S, WINDOW
from src.p7.types import P7Error
from src.p8.hazard_collision_stop import COLLISION_END, HazardCollisionStop
from src.scenario.research_noa_lifecycle import retain_first_error

# Leave room for the scene/services envelope below the default 128 KiB CSV field
# limit. This is a storage chunk bound, not a sampling or research threshold.
_RAW_BATCH_JSON_CHAR_LIMIT = 64 * 1024
_DRIVING_EVENT_POLICY = "OWNED_OBSTACLE_COLLISION_CONFIRM_STOP_OTHER_EVENTS_CONTINUE"
_PROGRAM_VERSION = "P8_M2_COLLISION_CONFIRM_STOP_V1"


class OperatorM2Hazard:
    backend_type = MovingHazardBackend
    scenario_version = SCENARIO_VERSION
    program_version = _PROGRAM_VERSION
    def __init__(
        self, session, services, config, occurrences, sink, started_at, provenance
    ):
        self.session, self.services, self.config = session, services, config
        self.occurrences, self.sink = occurrences, sink
        self.started_at = started_at
        self.planned = Random(config.seed).uniform(*WINDOW)
        self.state = "SCHEDULED"
        self.closed = False
        self.claimed = False
        self.elapsed = 0.0
        self.next_attempt = self.planned - config.preparation_s
        self.preparation_s = None
        self.command_at = None
        self.first_geometry = self.first_display = None
        self.last_reason = None
        self.collision_stop = None
        self._road_departure_active = False
        self._driving_event_counts = {"COLLISION": 0, "ROAD_DEPARTURE_PROXY": 0}
        self.last_frame = None
        self.raw = []
        self._raw_pending_batch = None
        self._raw_confirmed_batches = 0
        self._raw_confirmed_samples = 0
        self._raw_write_errors = []
        self.last_flush = 0.0
        self.last_geometry = None
        self.display_frames_checked = 0
        self.display_frames_unmatched = 0
        self._lead_complete = False
        self._passed = False
        self.provenance = provenance
        self.backend = self.backend_type(session, config, self.emit)
        self.origin_stamp = services.stamp()
        self.emit(
            "scheduled",
            {
                "seed": config.seed,
                "distribution": "UNIFORM_CLOSED_400_600",
                "planned_exposure_active_s": self.planned,
                "window_s": list(WINDOW),
                "m2_active_origin_monotonic_s": started_at,
                "origin_record_monotonic_ns": self.origin_stamp.host.monotonic_ns,
                "target_ttc_s": TARGET_TTC_S,
                "preparation_range_s": [
                    min(config.preparation_s, config.lane_change_s),
                    config.preparation_s,
                ],
                "tuning": asdict(config),
                "provenance": provenance,
                "response_thresholds": "UNDECIDED",
                "driving_event_policy": _DRIVING_EVENT_POLICY,
            },
        )
        try:
            self.backend.start_observation()
        except BaseException as error:
            retain_first_error(
                lambda primary=error: self.finalize("SETUP_ERROR", primary), error
            )
            raise

    def emit(self, name, payload):
        event_observed_qpc = observation_ns()
        # P5/interaction observers can queue older stamped events after telemetry.
        # Drain them before a newly stamped scene event advances the shared clock.
        self.session.persistence.flush_events()
        self.services.emit(
            f"m2_hazard_{name}",
            {
                "scenario_version": self.scenario_version,
                "m2_active_elapsed_s": self.elapsed,
                "program_version": self.program_version,
                "auto_state": self.session.bundle.automation_runtime.state.control_mode.value,
                "scene_state": self.state,
                "event_observed_perf_counter_ns": event_observed_qpc,
                **payload,
            },
        )

    def _p5_blocker(self):
        coordinator = self.session.exit_assistance
        tm = self.session.bundle.traffic_manager_backend
        if tm is not None and tm.lane_change_in_flight is not None:
            return "P5_OR_GENERAL_LANE_CHANGE_IN_FLIGHT"
        if coordinator is not None and getattr(
            coordinator, "_automatic_route_cancel_unverified", False
        ):
            return "P5_EXIT_ROUTE_CANCELLATION_UNVERIFIED"
        if coordinator is not None and (
            coordinator.lane_change_in_progress
            or coordinator.status.value
            not in ("COMPLETED", "NOT_PRESENTED", "INTERRUPTED")
        ):
            return f"P5_CONFLICT:{coordinator.status.value}"
        return None

    def record_input_clock(self, mono_start, mono_end, qpc_start, qpc_end):
        self._input_clock = (mono_start, mono_end, qpc_start, qpc_end)

    def after_control(self, driver_input, poll_started_ns, poll_completed_ns):
        if self.closed or self.collision_stop is not None:
            return
        sample = self.capture_raw_input(driver_input, poll_started_ns, poll_completed_ns)
        self._advance_scene(sample)

    def capture_raw_input(self, driver_input, poll_started_ns, poll_completed_ns):
        """Keep recording input even after the scene itself has been invalidated."""
        self.elapsed = self.session.monotonic_clock() - self.started_at
        persistence = self.session.persistence
        sample = persistence.source.last_snapshot_sample
        if (
            sample is None
            or sample.timestamp.carla_snapshot is None
            or sample.world_snapshot is None
        ):
            raise P7Error("M2_V1_CACHED_WORLD_SNAPSHOT_REQUIRED")
        stamp = sample.timestamp
        input_clock = getattr(self, "_input_clock", None)
        self._input_clock = None
        qpc_bounds = (
            input_clock[2:] if input_clock is not None
            and input_clock[:2] == (poll_started_ns, poll_completed_ns)
            else (None, None)
        )
        if self.elapsed - self.last_flush >= 1.0 or len(self.raw) >= 120:
            self._flush_raw()
        self.raw.append(
            {
                "poll_started_monotonic_ns": poll_started_ns,
                "poll_completed_monotonic_ns": poll_completed_ns,
                "poll_started_perf_counter_ns": qpc_bounds[0],
                "poll_completed_perf_counter_ns": qpc_bounds[1],
                "input": asdict(driver_input),
                "m2_active_elapsed_s": self.elapsed,
                "observation_monotonic_ns": stamp.host.monotonic_ns,
                "observation_perf_counter_ns": stamp.host.perf_counter_ns,
                "frame": stamp.carla_snapshot.frame,
                "simulation_seconds": stamp.carla_snapshot.simulation_seconds,
                "auto_state": self.session.bundle.automation_runtime.state.control_mode.value,
                "ego_speed_mps": sample.speed_kmh / 3.6,
                "applied_control_readback": {
                    "throttle": sample.control.throttle,
                    "brake": sample.control.brake,
                    "steer": sample.control.steer,
                },
                "scene_state": self.state,
            }
        )
        return sample

    def _advance_scene(self, sample):
        stamp = sample.timestamp
        failure = self.backend.failures(sample)
        if failure is not None and failure["reason"] not in self._driving_event_counts:
            self.state, self.last_reason = "ABORTED", failure["reason"]
            self.emit("unexpected_failure", failure)
            raise P7Error(f"M2_V1_{self.last_reason}")
        self._record_driving_event(failure)
        if self.collision_stop is not None:
            return
        if (
            self.last_frame is not None
            and stamp.carla_snapshot.frame <= self.last_frame
        ):
            return
        self.last_frame = stamp.carla_snapshot.frame
        if self.elapsed >= 780:
            return
        if (
            self.elapsed > WINDOW[1]
            and self.first_geometry is None
            and self.state == "LEAD_MOVING_ASIDE"
        ):
            self.state, self.last_reason = "NOT_OCCURRED", "NO_EXPOSURE_BY_WINDOW_END"
            self.emit("not_occurred", {"reason": self.last_reason})
        if self.state == "SCHEDULED":
            if self.elapsed > WINDOW[1]:
                self.state = "NOT_OCCURRED"
                self.emit(
                    "not_occurred",
                    {"reason": self.last_reason or "WINDOW_EXPIRED_FRAME_GAP"},
                )
            elif self.elapsed >= self.next_attempt:
                self.next_attempt = self.elapsed + self.config.retry_s
                # Reuse the remaining lead-in to the same scheduled exposure.
                # Once it is shorter, retain one existing lane-change duration
                # for preparation instead of restarting a six-second horizon.
                preparation_s = min(
                    self.config.preparation_s,
                    max(self.config.lane_change_s, self.planned - self.elapsed),
                )
                reason = (
                    "ROAD_DEPARTURE_PROXY_ACTIVE"
                    if self._road_departure_active
                    else "COLLISION_OBSERVED_THIS_SAMPLE"
                    if failure is not None
                    else self._p5_blocker()
                )
                route_attempt = None
                if reason is None:
                    try:
                        route = self.backend.preflight(sample, preparation_s)
                    except P7Error as error:
                        reason = str(error)
                    route_attempt = self.backend.preparation_attempt
                if reason is not None:
                    self.last_reason = reason
                    self.emit(
                        "preparation_deferred",
                        {
                            "reason": reason,
                            "planned_active_s": self.planned,
                            "preparation_s": preparation_s,
                            "route_attempt": route_attempt,
                        },
                    )
                elif self.elapsed + preparation_s > WINDOW[1]:
                    self.state, self.last_reason = (
                        "NOT_OCCURRED",
                        "INSUFFICIENT_PREPARATION_TIME_IN_WINDOW",
                    )
                    self.emit("not_occurred", {"reason": self.last_reason})
                else:
                    self.occurrences.claim(
                        str(self.services.context.participant_id),
                        str(self.services.context.study_run_id),
                    )
                    self.claimed = True
                    self.preparation_s = preparation_s
                    self.state = "PREPARING"
                    self.emit("preparation_started", route)
                    try:
                        delay = self.backend.prepare(sample)
                    except BaseException as error:
                        self.last_reason = f"PREPARATION_FAILED:{error}"
                        self.emit("preparation_failed", {"reason": self.last_reason})
                        raise
                    exposure_target = max(self.planned, self.elapsed + preparation_s)
                    self.command_at = exposure_target - delay
                    self.state = "OCCLUDED"
                    self.emit(
                        "exposure_plan",
                        {
                            "planned_active_s": self.planned,
                            "adjusted_target_active_s": exposure_target,
                            "command_target_active_s": self.command_at,
                            "predicted_partial_exposure_delay_s": delay,
                            "preparation_s": preparation_s,
                            "delay_reason": self.last_reason,
                        },
                    )
        geometry = self.backend.geometry(sample.world_snapshot)
        if geometry is None:
            if (
                self.state == "OCCLUDED"
                and self.backend.cargo_placement_frame is None
                and self.command_at is not None
                and self.elapsed >= self.command_at
            ):
                self.state, self.last_reason = (
                    "ABORTED", "CARGO_PLACEMENT_UNCONFIRMED"
                )
                self.emit(
                    "unexpected_failure",
                    {
                        "reason": self.last_reason,
                        "command_target_active_s": self.command_at,
                        "placement_observation": (
                            self.backend.cargo_placement_observation
                        ),
                    },
                )
                raise P7Error("M2_V1_CARGO_PLACEMENT_UNCONFIRMED")
            return
        self.last_geometry = geometry
        # Preserve each observed frame for later TTC reconstruction, without per-frame disk writes.
        if self.raw:
            self.raw[-1]["geometry"] = geometry
        if not geometry["cargo_stationary"]:
            self.last_reason = "CARGO_MOVED"
            self.state = "ABORTED"
            self.emit(
                "unexpected_failure",
                {"reason": self.last_reason, "observation": geometry},
            )
            raise P7Error("M2_V1_CARGO_MOVED")
        if geometry["partial_visible_proxy"] and self.first_geometry is None:
            observed_elapsed = (
                geometry.get(
                    "snapshot_callback_runtime_monotonic_s",
                    self.started_at + self.elapsed,
                )
                - self.started_at
            )
            valid = (
                WINDOW[0] <= observed_elapsed <= WINDOW[1]
                and self.backend.motion_started is not None
            )
            self.first_geometry = {
                "m2_active_elapsed_s": observed_elapsed,
                "loop_read_active_elapsed_s": self.elapsed,
                "active_time_basis": "WORLD_FRAME_CALLBACK"
                if "snapshot_callback_runtime_monotonic_s" in geometry
                else "LOOP_READ_CALLBACK_UNAVAILABLE",
                "observation_monotonic_ns": stamp.host.monotonic_ns,
                "planned_delta_s": observed_elapsed - self.planned,
                "normal_window_candidate": valid,
                **geometry,
            }
            self.state = "EXPOSED" if valid else "INVALID_EXPOSURE"
            if not valid:
                self.last_reason = "EARLY_LATE_OR_UNCOMMANDED_EXPOSURE"
            self.emit("geometric_exposure", self.first_geometry)
            if not valid:
                raise P7Error("M2_V1_INVALID_EXPOSURE")
        if (
            self.state == "OCCLUDED"
            and self.command_at is not None
            and self.elapsed >= self.command_at
        ):
            if self.elapsed > WINDOW[1]:
                self.state, self.last_reason = "NOT_OCCURRED", "LATE_COMMAND_SUPPRESSED"
                self.emit("not_occurred", {"reason": self.last_reason})
                raise P7Error("M2_V1_PREPARED_WINDOW_EXPIRED")
            elif self._p5_blocker() is not None:
                self.last_reason = self._p5_blocker()
            else:
                self.backend.begin_reveal(geometry["simulation_seconds"])
                self.state = "LEAD_MOVING_ASIDE"
                self.emit(
                    "lead_move_command",
                    {
                        "command_monotonic_ns": self.services.stamp().host.monotonic_ns,
                        "observation": geometry,
                        "target_ttc_s": TARGET_TTC_S,
                    },
                )
        self.backend.advance(sample, geometry)
        if geometry["lead_move_observed_complete"] and not self._lead_complete:
            self._lead_complete = True
            self.emit(
                "lead_move_observed", {"observation": geometry, "driver_success": None}
            )
        if geometry["passed"] and not self._passed:
            self._passed = True
            self.emit("cargo_passed", {"observation": geometry, "driver_success": None})
        if self.backend.retire_if_behind(geometry):
            self.state = "SCENE_RETIRED"

    def _record_driving_event(self, observation):
        # Driving outcomes are research observations, not session failures.
        # Preserve every collision callback; log road departure at transitions
        # rather than writing the same condition on every host loop.
        reason = None if observation is None else observation["reason"]
        if reason == "COLLISION":
            self._driving_event_counts[reason] += len(observation["collisions"])
            cargo = self.backend.cargo
            matching = [
                collision for collision in observation["collisions"]
                if cargo is not None and collision["actor_id"] == cargo.id
            ]
            if matching and self.collision_stop is None:
                self.collision_stop = HazardCollisionStop(self, matching)
                self.state = "COLLISION"
                self.last_reason = COLLISION_END
        elif reason == "ROAD_DEPARTURE_PROXY":
            if self._road_departure_active:
                return
            self._road_departure_active = True
            self._driving_event_counts[reason] += 1
        else:
            if self._road_departure_active:
                self._road_departure_active = False
                self.emit(
                    "road_departure_proxy_cleared",
                    {"driving_event_policy": _DRIVING_EVENT_POLICY},
                )
            return
        self.emit(
            "driving_event",
            {
                **observation,
                "driving_event_policy": _DRIVING_EVENT_POLICY,
                "session_action": "AWAIT_COLLISION_CONFIRMATION"
                if self.collision_stop is not None else "CONTINUE",
            },
        )

    def display_exposure_valid(self, geometry):
        return True

    def after_display_completed(self, frame):
        if (
            self.closed
            or self.collision_stop is not None
            or self.first_display is not None
            or frame is None
            or frame.frame is None
            or frame.simulation_seconds is None
        ):
            return
        geometry = self.backend.display_geometry(frame)
        self.display_frames_checked += 1
        if geometry is None:
            self.display_frames_unmatched += 1
        if geometry is None or geometry["partial_visible_proxy"] is not True:
            return
        self.first_display = {
            "sensor_actor_id": frame.sensor_actor_id,
            "frame": frame.frame,
            "simulation_seconds": frame.simulation_seconds,
            "receipt_perf_counter_seconds": frame.receipt_perf_counter_seconds,
            "flip_completed_monotonic_ns": frame.flip_completed_monotonic_ns,
            "flip_completed_perf_counter_ns": frame.flip_completed_perf_counter_ns,
            "method": "FRAME_MATCHED_PROJECTED_BOUNDS_PARTIAL_EXPOSURE_PROXY",
            "uncertainty": "MESH_PIXELS_OTHER_OCCLUDERS_PHOTONS_AND_PERCEPTION_UNVALIDATED",
            "hazard_visible_t": None,
            "geometry": geometry,
        }
        frame_elapsed = (
            geometry["snapshot_callback_runtime_monotonic_s"] - self.started_at
        )
        valid = (
            WINDOW[0] <= frame_elapsed <= WINDOW[1]
            and self.backend.motion_started is not None
            and frame.simulation_seconds >= self.backend.motion_started
            and self.display_exposure_valid(geometry)
        )
        self.first_display["frame_callback_active_elapsed_s"] = frame_elapsed
        self.first_display["normal_window_candidate"] = valid
        self.emit("display_candidate", self.first_display)
        if not valid:
            self.state, self.last_reason = (
                "INVALID_EXPOSURE",
                "EARLY_LATE_OR_UNCOMMANDED_DISPLAY",
            )
            raise P7Error("M2_V1_INVALID_DISPLAY_EXPOSURE")

    def _next_raw_batch(self):
        payload = {
            "input_time_basis": "EXISTING_POLL_HOST_MONOTONIC_BOUNDS",
            "batch_id": (
                f"{self.services.context.study_run_id}:m2:"
                f"{self.origin_stamp.host.monotonic_ns}:{self._raw_confirmed_batches}"
            ),
            "sample_start_index": self._raw_confirmed_samples,
            # This upper bound reserves enough digits for the actual chunk size.
            "sample_count": len(self.raw),
            "write_attempt_id": "0" * 32,
            "retry_after_write_error": False,
            "samples": [],
            "geometry_latest": self.last_geometry,
        }
        # Match SessionServices.emit's JSON encoding. Size at flush time so the
        # per-frame input/control path gains no extra serialization.
        batch_chars = len(json.dumps(payload, allow_nan=False))
        for sample in self.raw:
            sample_chars = len(json.dumps(sample, allow_nan=False))
            added_chars = sample_chars + (2 if payload["samples"] else 0)
            if batch_chars + added_chars > _RAW_BATCH_JSON_CHAR_LIMIT:
                if not payload["samples"]:
                    raise P7Error("M2_V1_RAW_SAMPLE_EXCEEDS_CSV_CHUNK_LIMIT")
                break
            batch_chars += added_chars
            payload["samples"].append(sample)
        payload["sample_count"] = len(payload["samples"])
        return payload

    def _flush_raw(self):
        while self.raw:
            if self._raw_pending_batch is None:
                self._raw_pending_batch = self._next_raw_batch()
            # Keep the same samples, geometry and batch ID after an unconfirmed
            # write. Fixed-width attempt IDs cannot enlarge a retried chunk.
            payload = {**self._raw_pending_batch, "write_attempt_id": uuid4().hex}
            if len(json.dumps(payload, allow_nan=False)) > _RAW_BATCH_JSON_CHAR_LIMIT:
                raise P7Error("M2_V1_RAW_SAMPLE_EXCEEDS_CSV_CHUNK_LIMIT")
            write_error = retain_first_error(
                lambda batch=payload: self.emit("raw_samples", batch), None
            )
            if write_error is not None:
                self._raw_pending_batch["retry_after_write_error"] = True
                self._raw_write_errors.append(
                    {
                        key: payload[key]
                        for key in (
                            "batch_id",
                            "write_attempt_id",
                            "sample_start_index",
                            "sample_count",
                        )
                    }
                    | {
                        "outcome": "WRITE_UNCONFIRMED",
                        "error": f"{type(write_error).__name__}:{write_error}",
                    }
                )
                raise write_error
            # writerow may have succeeded before a failed flush. Only remove a
            # chunk after acknowledgement; repeated batch IDs identify retries.
            del self.raw[: payload["sample_count"]]
            self._raw_confirmed_samples += payload["sample_count"]
            self._raw_confirmed_batches += 1
            self._raw_pending_batch = None
        self.last_flush = self.elapsed

    def _raw_storage_record(self):
        return {
            "status": "UNCONFIRMED_LOG_STORAGE_FAILURE"
            if getattr(getattr(self.services, "logger", None), "collection_failure_count", 0)
            else "WRITE_UNCONFIRMED"
            if self.raw
            else "ACKNOWLEDGED_AFTER_RETRY"
            if self._raw_write_errors
            else "ACKNOWLEDGED",
            "acknowledged_batch_count": self._raw_confirmed_batches,
            "acknowledged_sample_count": self._raw_confirmed_samples,
            "pending_sample_count": len(self.raw),
            "pending_batch_id": None
            if self._raw_pending_batch is None
            else self._raw_pending_batch["batch_id"],
            "possible_duplicate_batches": bool(self._raw_write_errors),
            "duplicate_detection_key": "batch_id",
            "write_errors": list(self._raw_write_errors),
        }

    def finalize(self, reason, error=None):
        if self.closed:
            return
        self.closed = True
        self.elapsed = (
            self.collision_stop.active_seconds if self.collision_stop is not None
            else self.session.monotonic_clock() - self.started_at
        )
        if self.collision_stop is not None:
            reason = COLLISION_END
        if self.state == "SCHEDULED":
            self.state = "NOT_OCCURRED"
            self.last_reason = self.last_reason or "M2_ENDED_WITHOUT_USABLE_OPPORTUNITY"
        cleanup = self.cleanup_backend()
        cleanup_failed = bool(cleanup["failures"] or cleanup["remaining_actor_ids"])
        pending_collisions = cleanup.get("pending_collisions", [])
        self._driving_event_counts["COLLISION"] += len(pending_collisions)
        primary_error = error
        finalization_errors = []

        def retain_failure(step, failure):
            nonlocal primary_error
            finalization_errors.append(
                {"step": step, "error": f"{type(failure).__name__}:{failure}"}
            )
            if primary_error is None:
                primary_error = failure
            else:
                append_error_note(
                    primary_error,
                    f"Secondary M2 finalization failure ({step}): "
                    f"{type(failure).__name__}:{failure}",
                )

        def attempt(step, action):
            failure = retain_first_error(action, None)
            if failure is not None:
                retain_failure(step, failure)

        if cleanup_failed:
            retain_failure("cleanup", P7Error(f"M2_V1_CLEANUP_INCOMPLETE:{cleanup}"))
        # Final raw storage must be attempted before declaring a normal scene
        # end. Cleanup has already run, so a storage error cannot strand actors.
        attempt("raw_flush", self._flush_raw)
        record = {
            **canonical_hazard_record(None),
            "scenario_version": self.scenario_version,
            "program_version": self.program_version,
            "auto_state_at_m2_scene_close": self.session.bundle.automation_runtime.state.control_mode.value,
            "execution_scope": "DEV_M2_INTERNAL",
            "execution_status": "FAILED"
            if primary_error is not None
            else "CONTROLLED_COLLISION_STOP" if self.collision_stop is not None
            else "DEVELOPMENT_RUN_ENDED",
            "seed": self.config.seed,
            "distribution": "UNIFORM_CLOSED_400_600",
            "window_s": list(WINDOW),
            "planned_exposure_active_s": self.planned,
            "preparation_s": self.preparation_s,
            "m2_active_origin_monotonic_s": self.started_at,
            "m2_active_elapsed_s": self.elapsed,
            "hazard_state": self.state,
            "end_reason": reason,
            "scene_reason": self.last_reason,
            "error": None
            if primary_error is None
            else f"{type(primary_error).__name__}:{primary_error}",
            "occurrence_reserved": self.claimed,
            "first_geometry": self.first_geometry,
            "display_candidate": self.first_display,
            "target_ttc_s": TARGET_TTC_S,
            "display_frames_checked": self.display_frames_checked,
            "display_frames_unmatched": self.display_frames_unmatched,
            "display_status": "FRAME_MATCHED_PROXY_CANDIDATE"
            if self.first_display
            else "NO_VALIDATED_DISPLAY_CANDIDATE",
            "driver_response_outcome": "COLLISION" if self.collision_stop is not None else None,
            "collision_stop": None if self.collision_stop is None else self.collision_stop.record,
            "provenance": self.provenance,
            "occurrence_result": (
                "INVALID_EXPOSURE"
                if self.state == "INVALID_EXPOSURE"
                else "EXPOSURE_CANDIDATE"
                if self.first_geometry is not None or self.first_display is not None
                else "NOT_OCCURRED"
            ),
            "cleanup": cleanup,
            "driving_event_policy": _DRIVING_EVENT_POLICY,
            "driving_event_counts": dict(self._driving_event_counts),
            "road_departure_proxy_active_at_end": self._road_departure_active,
            "raw_storage": self._raw_storage_record(),
            "finalization_errors": finalization_errors,
            "partial_data": reason != "M2_END" or primary_error is not None,
        }
        attempt("end_event", lambda: self.emit("end", record))
        attempt("event_flush", self.session.persistence.flush_events)
        if primary_error is not None:
            record.update(
                execution_status="FAILED",
                partial_data=True,
                error=f"{type(primary_error).__name__}:{primary_error}",
            )
        # Keep the in-memory record usable for the manifest even if CSV storage
        # failed; the end-event write itself may also be unconfirmed.
        attempt("record_sink", lambda: self.sink(record))
        if error is None and primary_error is not None:
            raise primary_error

    def cleanup_backend(self):
        return self.backend.cleanup()
