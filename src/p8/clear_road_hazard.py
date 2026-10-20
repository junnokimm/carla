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
    scenario_version = "M2_CLEAR_ROAD_WARNING_V2_20261020"
    program_version = "P8_CLEAR_ROAD_WARNING_AUDIO_V5"

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
            payload = {**payload, "exposure_policy": "CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD",
                       "preparation_range_s": None,
                       "readiness_check_starts_before_planned_s": self.config.preparation_s,
                       "event_order_policy": POLICY,
                       "automatic_avoidance": "NONE", "warning_modality": "VISUAL_AND_AUDIO",
                       "warning_audio_cue_id": CUE_ID,
                       "audio_failure_policy": "KEEP_VISUAL_CONTINUE"}
        elif name == "end":
            payload = {**payload, "event_order_policy": POLICY,
                       "exit_sequence_at_reveal": self.exit_sequence_at_reveal}
        super().emit(name, payload)

    def _store_record(self, record):
        record.update(warning=self.warning_record, warning_errors=list(self.warning_errors),
                      warning_audio=self.warning_audio.summary(),
                      exposure_policy="CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD",
                      target_speed_kmh=self.target_kmh,
                      readiness_tuning={key: getattr(self.config, key) for key in (
                          "speed_tolerance_kmh", "stable_speed_s", "readiness_max_gap_s")},
                      last_readiness=self.last_readiness,
                      event_order_policy=POLICY,
                      exit_sequence_at_reveal=self.exit_sequence_at_reveal,
                      nonfatal_recording_errors=list(self.continuation_errors),
                      automatic_avoidance="NONE")
        super()._store_record(record)

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
        if self.elapsed >= 780:
            return
        if self.state in ("SCHEDULED", "READY_HIDDEN"):
            if self.elapsed > WINDOW[1]:
                self.state = "NOT_OCCURRED"
                blocker = self._p5_blocker()
                self.last_reason = blocker or self.last_reason or "READINESS_WINDOW_EXPIRED"
                self.backend.begin_readiness(sample, skipped_reason=self.last_reason)
                self._record_readiness(sample, blocker, self.last_reason)
                self._try_record(lambda: self.emit(
                    "not_occurred", {"reason": self.last_reason, "session_action": "CONTINUE",
                                     "readiness": self.last_readiness}))
                self.cleanup_backend()
                return
            if self.elapsed < self.planned - self.config.preparation_s:
                return
            lane = (sample.road_id, sample.section_id, sample.lane_id)
            blocker = ("ROAD_DEPARTURE_PROXY_ACTIVE" if self._road_departure_active
                       else "COLLISION_OBSERVED_THIS_SAMPLE" if failure else self._p5_blocker())
            ready = (blocker is None and self.target_kmh is not None
                     and isfinite(sample.speed_kmh)
                     and abs(sample.speed_kmh - self.target_kmh) <= self.config.speed_tolerance_kmh)
            if (not ready or lane != self.ready_lane or self.previous_ready_time is None
                    or self.elapsed - self.previous_ready_time > self.config.readiness_max_gap_s):
                self.stable_since = self.elapsed if ready else None
                if lane != self.ready_lane:
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
                conditions = self.backend.preflight(sample)
            except Exception as error:
                self.clear_since = self.stable_since = None
                self._record_readiness(sample, None, str(error))
                if not isinstance(error, P7Error):
                    raise
                self._defer(str(error))
                return
            target = conditions["target_speed_kmh"]
            if target != self.target_kmh:
                self.target_kmh, self.stable_since = target, self.elapsed
            if self.clear_since is None:
                self.clear_since = self.elapsed
            if self.backend.cargo is None:
                self.backend.prepare(sample)
                self.state = "READY_HIDDEN"
            if (self.elapsed < self.planned or self.stable_since is None
                    or self.elapsed - max(self.stable_since, self.clear_since) < self.config.stable_speed_s):
                self._record_readiness(
                    sample, None, "WAITING_FOR_PLANNED_TIME_AND_STABLE_SPEED_CLEAR_ROAD", True
                )
                self._defer("WAITING_FOR_PLANNED_TIME_AND_STABLE_SPEED_CLEAR_ROAD")
                return
            self._record_readiness(sample, None, "READY_FOR_REVEAL", True)
            # Keep proof alongside exposure even if diagnostic event writing fails.
            self.exit_sequence_at_reveal = sequence_evidence(self.session.exit_assistance)
            self.occurrences.claim(str(self.services.context.participant_id),
                                   str(self.services.context.study_run_id))
            self.claimed = True
            self.preparation_s = 0.0
            self.backend.reveal(sample)
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
        if self.raw:
            self.raw[-1]["geometry"] = geometry
        if not geometry["cargo_stationary"]:
            raise P7Error("CARGO_MOVED")
        if self.first_geometry is None:
            observed_elapsed = geometry.get("snapshot_callback_runtime_monotonic_s",
                                            self.started_at + self.elapsed) - self.started_at
            valid = (WINDOW[0] <= observed_elapsed <= WINDOW[1]
                     and abs(geometry["ego_speed_mps"] * 3.6 - self.target_kmh) <= self.config.speed_tolerance_kmh
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
        return (self.target_kmh is not None and geometry["collision_path_proxy"]
                and abs(geometry["ego_speed_mps"] * 3.6 - self.target_kmh) <= self.config.speed_tolerance_kmh)

    def draw_warning(self, surface):
        """Overlay only. Never consume events or write control/automation state."""
        self.warning_drawn = False
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
        self.warning_audio.close()
        super().finalize(reason, error)
