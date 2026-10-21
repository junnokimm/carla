"""Production M2 clear-road opportunity; faults invalidate only the scene."""

from math import isfinite

from src.p7.clear_road_backend import ClearRoadHazardBackend
from src.p7.moving_config import WINDOW
from src.p7.types import P7Error
from src.p8.exit_before_hazard import POLICY, sequence_blocker, sequence_evidence
from src.p8.hazard_readiness import readiness_record
from src.p8.operator_m2_continuation import ContinuingM2Hazard
from src.scenario.driver_hud import DRIVER_NOTICE_TOP_RATIO
from src.scenario.hazard_warning_audio import CUE_ID, HazardWarningAudio


class ClearRoadM2Hazard(ContinuingM2Hazard):
    backend_type = ClearRoadHazardBackend
    scenario_version = "M2_CURRENT_SPEED_TTC4_EXTEND_V3_20261021"
    program_version = "P8_CLEAR_ROAD_WARNING_AUDIO_V5"
    exposure_policy = "CLEAR_ROAD_CURRENT_SPEED_TTC4_NO_LEAD"

    def __init__(self, *args, **kwargs):
        self.stable_since = self.previous_ready_time = self.ready_lane = None
        self.clear_since = self.target_kmh = self.reveal_at = None
        self.warning_started = self.warning_record = None
        self.warning_drawn = False
        self.warning_surfaces = {}
        self.warning_errors = []
        self.warning_audio = HazardWarningAudio()
        self.last_readiness = None
        self.exit_sequence_at_reveal = None
        self.duration_extension = None
        self._raw_retry_after = 0.0
        self._dropped_raw_samples = 0
        super().__init__(*args, **kwargs)
        self.warning_audio.prepare()

    def _p5_blocker(self):
        # Even NOT_PRESENTED/missing exit coordinators used to pass the generic
        # conflict check. Only this module's visibly presented, physically
        # resolved opportunity can unlock production M2 hazard preparation.
        return (sequence_blocker(self.session.exit_assistance)
                or super()._p5_blocker())

    def emit(self, name, payload):
        if name == "scheduled":
            payload = {**payload, "exposure_policy": self.exposure_policy,
                       "planned_draw_window_s": list(WINDOW),
                       "window_s": [self.planned, None],
                       "expiry_policy": "WAIT_FOR_SUITABLE_SECTION_EXTEND_M2",
                       "preparation_range_s": None,
                       "readiness_check_starts_before_planned_s": self.config.preparation_s,
                       "event_order_policy": POLICY,
                       "automatic_avoidance": "NONE", "warning_modality": "VISUAL_AND_AUDIO",
                       "warning_audio_cue_id": CUE_ID,
                       "audio_failure_policy": "KEEP_VISUAL_CONTINUE"}
        elif name == "end":
            payload = {**payload, "event_order_policy": POLICY,
                       "exposure_policy": self.exposure_policy,
                       "planned_draw_window_s": list(WINDOW),
                       "window_s": [self.planned, None],
                       "duration_extension": self.duration_extension,
                       "placement": getattr(getattr(self, "backend", None), "reveal_record", None),
                       "exit_sequence_at_reveal": self.exit_sequence_at_reveal}
        # Raw storage needs its write acknowledgement/retry protocol. Other
        # event failures must not prevent reveal, warning, or continued driving.
        if name == "raw_samples":
            super().emit(name, payload)
        else:
            self._try_record(lambda: super(ClearRoadM2Hazard, self).emit(name, payload))

    def _store_record(self, record):
        record.update(warning=self.warning_record, warning_errors=list(self.warning_errors),
                      warning_audio=self.warning_audio.summary(),
                      exposure_policy=self.exposure_policy,
                      planned_draw_window_s=list(WINDOW), window_s=[self.planned, None],
                      duration_extension=self.duration_extension,
                      placement=getattr(getattr(self, "backend", None), "reveal_record", None),
                      target_speed_kmh=self.target_kmh,
                      readiness_tuning={key: getattr(self.config, key) for key in (
                          "minimum_exposure_speed_kmh", "stable_speed_s", "readiness_max_gap_s",
                          "post_exposure_drive_s")},
                      last_readiness=self.last_readiness,
                      event_order_policy=POLICY,
                      exit_sequence_at_reveal=self.exit_sequence_at_reveal,
                      nonfatal_recording_errors=list(self.continuation_errors),
                      automatic_avoidance="NONE")
        super()._store_record(record)

    def capture_raw_input(self, driver_input, poll_started_ns, poll_completed_ns):
        # Keep a bounded unacknowledged batch, retry at most once per second,
        # and continue physical readiness even if storage is unavailable.
        now = self.session.monotonic_clock() - self.started_at
        sample = self.session.persistence.source.last_snapshot_sample
        if now >= self._raw_retry_after:
            try:
                return super().capture_raw_input(driver_input, poll_started_ns, poll_completed_ns)
            except Exception as error:
                if not self._raw_write_errors or self._raw_pending_batch is None:
                    raise
                detail = f"{type(error).__name__}:{error}"
                if detail not in self.continuation_errors and len(self.continuation_errors) < 32:
                    self.continuation_errors.append(detail)
                self._raw_write_errors[:] = self._raw_write_errors[-32:]
                self._raw_retry_after = now + 1.0
        self.elapsed = now
        self._input_clock = None
        self._dropped_raw_samples += 1
        if sample is None or sample.world_snapshot is None:
            raise P7Error("M2_V1_CACHED_WORLD_SNAPSHOT_REQUIRED")
        return sample

    def _raw_storage_record(self):
        return {**super()._raw_storage_record(),
                "dropped_samples_during_storage_failure": self._dropped_raw_samples}

    def exposure_time_valid(self, elapsed):
        # 400–600 s selects a planned time, not a deadline to discard the event.
        return isfinite(elapsed) and elapsed >= self.planned

    def should_extend_drive(self, elapsed, nominal_duration):
        """Called only at the nominal boundary; never owns input or control."""
        if self.closed or elapsed < nominal_duration:
            return False
        onset = (None if self.first_display is None else
                 self.first_display.get("frame_callback_active_elapsed_s"))
        waiting = (self.collision_stop is not None or self.scene_failure is not None
                   or onset is None or elapsed < onset + self.config.post_exposure_drive_s)
        if self.duration_extension is None:
            if not waiting:
                return False
            self.duration_extension = {
                "policy": "WAIT_FOR_SUITABLE_SECTION_THEN_POST_EXPOSURE_DRIVE",
                "nominal_duration_s": nominal_duration,
                "started_active_s": elapsed, "status": "ACTIVE",
                "post_exposure_drive_s": self.config.post_exposure_drive_s,
                "technical_failure_policy": "KEEP_DRIVING_AWAIT_OPERATOR_EXIT",
            }
            self.emit("m2_extension_started", dict(self.duration_extension))
        self.duration_extension.update(
            actual_active_s=elapsed, extension_s=max(0.0, elapsed - nominal_duration),
            display_onset_active_s=onset,
            reason="SCENE_ERROR_AWAIT_OPERATOR" if self.scene_failure is not None else
            "WAITING_FOR_EXPOSURE" if onset is None else "POST_EXPOSURE_OBSERVATION",
        )
        if not waiting and self.duration_extension["status"] == "ACTIVE":
            self.duration_extension.update(status="COMPLETE", completed_active_s=elapsed)
            self.emit("m2_extension_completed", dict(self.duration_extension))
        return waiting

    def _defer(self, reason):
        self.last_reason = reason
        self._try_record(lambda: self.emit(
            "preparation_deferred", {"reason": reason, "session_action": "CONTINUE",
                                     "readiness": self.last_readiness}))

    def _record_readiness(self, sample, blocker, reason, preflight_complete=False):
        # New diagnostics must never invalidate a scene or stop driving.
        def record():
            self.last_readiness = readiness_record(
                self, sample, blocker, reason, preflight_complete
            )
            self.emit("readiness_evaluated", self.last_readiness)
        self._try_record(record)

    def _advance_scene(self, sample):
        failure = self.backend.failures(sample)
        if failure is not None and failure["reason"] not in self._driving_event_counts:
            raise P7Error(failure["reason"])
        self._record_driving_event(failure)
        frame = sample.timestamp.carla_snapshot.frame
        if self.collision_stop is not None or (self.last_frame is not None and frame <= self.last_frame):
            return
        self.last_frame = frame
        if self.state in ("SCHEDULED", "READY_HIDDEN"):
            if self.elapsed < self.planned - self.config.preparation_s:
                return
            lane = (sample.road_id, sample.section_id, sample.lane_id)
            blocker = ("ROAD_DEPARTURE_PROXY_ACTIVE" if self._road_departure_active
                       else "COLLISION_OBSERVED_THIS_SAMPLE" if failure else self._p5_blocker())
            ready = (blocker is None and isfinite(sample.speed_kmh)
                     and sample.speed_kmh >= self.config.minimum_exposure_speed_kmh)
            if (not ready or self.stable_since is None or lane != self.ready_lane or self.previous_ready_time is None
                    or self.elapsed - self.previous_ready_time > self.config.readiness_max_gap_s):
                self.stable_since = self.elapsed if ready else None
                self.clear_since = None
            self.ready_lane, self.previous_ready_time = lane, self.elapsed
            if self.elapsed < self.next_attempt:
                return
            self.next_attempt = self.elapsed + self.config.retry_s
            if blocker:
                self.clear_since = None
                self.backend.begin_readiness(sample, skipped_reason=blocker)
                self._record_readiness(sample, blocker, blocker)
                self._defer(blocker)
                return
            try:
                self.backend.preflight(sample)
            except Exception as error:
                self.clear_since = self.stable_since = None
                self._record_readiness(sample, None, str(error))
                if not isinstance(error, P7Error):
                    raise
                self._defer(str(error))
                return
            if self.clear_since is None:
                self.clear_since = self.elapsed
            if self.backend.cargo is None:
                self.backend.prepare(sample)
                self.state = "READY_HIDDEN"
            if (self.elapsed < self.planned or self.stable_since is None
                    or self.elapsed - max(self.stable_since, self.clear_since) < self.config.stable_speed_s):
                self._record_readiness(
                    sample, None, "WAITING_FOR_PLANNED_TIME_AND_ELIGIBLE_MOTION_CLEAR_ROAD", True
                )
                self._defer("WAITING_FOR_PLANNED_TIME_AND_ELIGIBLE_MOTION_CLEAR_ROAD")
                return
            # Keep proof alongside exposure even if diagnostic event writing fails.
            self.exit_sequence_at_reveal = sequence_evidence(self.session.exit_assistance)
            reservation_started = False
            def reserve():
                nonlocal reservation_started
                reservation_started = True
                self.occurrences.claim(str(self.services.context.participant_id),
                                       str(self.services.context.study_run_id))
                self.claimed = True
            self.preparation_s = 0.0
            try:
                self.backend.reveal(sample, before_placement=reserve)
            except P7Error as error:
                if reservation_started:
                    raise
                self.clear_since = self.stable_since = None
                self._record_readiness(sample, None, str(error))
                self._defer(str(error))
                return
            self._record_readiness(sample, None, "READY_FOR_REVEAL", True)
            self.reveal_at = self.elapsed
            self.state = "REVEAL_REQUESTED"
        if self.state == "NOT_OCCURRED":
            return
        geometry = self.backend.geometry(sample.world_snapshot)
        if geometry is None:
            if (self.first_geometry is None and self.reveal_at is not None
                    and self.elapsed - self.reveal_at > self.config.placement_timeout_s):
                raise P7Error("CARGO_PLACEMENT_OR_EXPOSURE_UNCONFIRMED")
            return
        self.last_geometry = geometry
        if self.raw and self.raw[-1]["frame"] == frame:
            self.raw[-1]["geometry"] = geometry
        if not geometry["cargo_stationary"]:
            raise P7Error("CARGO_MOVED")
        if self.first_geometry is None:
            observed_elapsed = geometry.get("snapshot_callback_runtime_monotonic_s",
                                            self.started_at + self.elapsed) - self.started_at
            valid = (self.exposure_time_valid(observed_elapsed)
                     and geometry["collision_path_proxy"]
                     and geometry["partial_visible_proxy"] is True)
            if not valid:
                raise P7Error("EXPOSURE_READINESS_INVALID")
            self.first_geometry = {**geometry, "m2_active_elapsed_s": observed_elapsed,
                                   "normal_window_candidate": True,
                                   "observation_monotonic_ns": sample.timestamp.host.monotonic_ns,
                                   "target_speed_kmh": self.target_kmh,
                                   "lead_vehicle_count": 0,
                                   "planned_delta_s": observed_elapsed - self.planned}
            self.state = "EXPOSED"
            self.emit("geometric_exposure", self.first_geometry)
        self.backend.advance(sample, geometry)
        if geometry["passed"] and not self._passed:
            self._passed = True
            self.emit("cargo_passed", {"observation": geometry, "driver_success": None})
        if self.backend.retire_if_behind(geometry):
            self.state = "SCENE_RETIRED"

    def display_exposure_valid(self, geometry):
        # Braking after placement is a response, not a failed speed match.
        return (geometry["collision_path_proxy"]
                and isfinite(geometry["ego_speed_mps"]) and geometry["ego_speed_mps"] >= 0)

    def draw_warning(self, surface):
        """Overlay only. Never consume events or write control/automation state."""
        self.warning_drawn = False
        if self.scene_failure is not None and self.duration_extension is not None and not self.closed:
            self._draw_extension_fault(surface)
            return
        if (self.scene_failure is not None or self.collision_stop is not None
                or self.first_display is None or self._passed or self.closed):
            return
        if self.warning_started is None:
            self.warning_started = self.elapsed
        if self.elapsed - self.warning_started >= self.config.warning_duration_s:
            return
        try:
            import pygame
            noa = self.session.bundle.automation_runtime.state.control_mode.value == "NOA_ACTIVE"
            if noa not in self.warning_surfaces:
                font = pygame.font.SysFont("malgungothic", 26, bold=True)
                text = "충돌 경고 · 전방 장애물!" + ("  직접 운전해 주세요 (인수 요청)" if noa else "")
                self.warning_surfaces[noa] = font.render(text, True, (255, 255, 255), (150, 20, 20))
            message = self.warning_surfaces[noa]
            surface.blit(message, (
                (surface.get_width() - message.get_width()) // 2,
                round(surface.get_height() * DRIVER_NOTICE_TOP_RATIO),
            ))
            self.warning_drawn = True
        except Exception as error:  # noqa: BLE001 -- display fault cannot stop driving
            detail = f"{type(error).__name__}:{error}"
            if detail not in self.warning_errors:
                self.warning_errors.append(detail)
                self._try_record(lambda: self.emit("warning_failed_continue", {"error": detail}))

    def _draw_extension_fault(self, surface):
        # A failed scene cannot satisfy the extension. Keep input available and
        # explicitly leave the decision to end the run with the operator.
        try:
            import pygame
            if "extension_fault" not in self.warning_surfaces:
                font = pygame.font.SysFont("malgungothic", 24, bold=True)
                self.warning_surfaces["extension_fault"] = font.render(
                    "위험사건 준비 오류 · 진행자 확인 필요 (ESC: 종료 및 저장 시도)",
                    True, (255, 255, 255), (100, 40, 20),
                )
            message = self.warning_surfaces["extension_fault"]
            surface.blit(message, ((surface.get_width() - message.get_width()) // 2,
                                   round(surface.get_height() * DRIVER_NOTICE_TOP_RATIO)))
        except Exception as error:
            detail = f"{type(error).__name__}:{error}"
            if detail not in self.warning_errors:
                self.warning_errors.append(detail)

    def after_warning_flip(self, mono_ns, qpc_ns):
        if not self.warning_drawn or self.warning_record is not None:
            return
        noa = self.session.bundle.automation_runtime.state.control_mode.value == "NOA_ACTIVE"
        # This hook runs after the visible warning's first successful flip. A
        # finite cue is requested once, with no control changes or blocking wait.
        audio = self.warning_audio.play()
        self.warning_record = {"collision_warning": True, "takeover_request": noa,
                               "modality": "VISUAL_AND_AUDIO_REQUESTED" if audio["play_request_accepted"] else "VISUAL",
                               "audio": audio, "flip_completed_monotonic_ns": mono_ns,
                               "flip_completed_perf_counter_ns": qpc_ns,
                               "obstacle_flip_perf_counter_ns": self.first_display.get("flip_completed_perf_counter_ns"),
                               "automatic_avoidance": "NONE", "changes_control_authority": False}
        self._try_record(lambda: self.emit("warning_displayed", self.warning_record))

    def finalize(self, reason, error=None):
        if self.duration_extension is not None:
            elapsed = (self.collision_stop.active_seconds if self.collision_stop is not None
                       else self.session.monotonic_clock() - self.started_at)
            self.duration_extension.update(
                actual_active_s=elapsed,
                extension_s=max(0.0, elapsed - self.duration_extension["nominal_duration_s"]),
                end_reason=reason,
            )
            if self.duration_extension["status"] == "ACTIVE":
                self.duration_extension["status"] = "STOPPED_BEFORE_COMPLETION"
        self.warning_audio.close()
        super().finalize(reason, error)
