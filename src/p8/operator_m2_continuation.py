"""Contain scene failures; the M2 driving window owns session completion."""

from src.p8.operator_m2_hazard import OperatorM2Hazard


class ContinuingM2Hazard(OperatorM2Hazard):
    def __init__(
        self, session, services, config, occurrences, sink, started_at, provenance
    ):
        self.scene_failure = None
        self.cleanup_result = None
        self.continuation_errors = []
        self._last_record = {}
        self._result_sink = sink
        self._initializing = True
        try:
            super().__init__(
                session,
                services,
                config,
                occurrences,
                self._store_record,
                started_at,
                provenance,
            )
        except Exception as error:  # noqa: BLE001 -- isolate scene from the driving session
            self.invalidate(error, "SETUP")
        finally:
            self._initializing = False

    def _store_record(self, record):
        if self.scene_failure is not None:
            record.update(self._failure_fields())
        self._last_record = record
        self._result_sink(record)

    def _failure_fields(self):
        return {
            "execution_status": "FAILED",
            "hazard_state": "FAILED",
            "occurrence_result": "INVALID",
            "scene_reason": self.last_reason,
            "session_action": "CONTINUE",
            "partial_data": True,
            "analysis_eligible": False,
            "scene_error_policy": "INVALIDATE_SCENE_CONTINUE_MODULE",
        }

    def emit(self, name, payload):
        if self.scene_failure is not None:
            payload = {**payload, **self._failure_fields()}
        super().emit(name, payload)

    def invalidate(self, error, phase="RUNTIME"):
        if self.scene_failure is not None:
            return
        self.scene_failure = error
        self.state = "FAILED"
        self.last_reason = f"{type(error).__name__}:{error}"
        self.cleanup_backend()
        self._try_record(
            lambda: self.emit(
                "scene_failed_continue",
                {
                    "failure_phase": phase,
                    "reason": self.last_reason,
                    "session_action": "CONTINUE",
                    "occurrence_result": "INVALID",
                    "cleanup": self.cleanup_result,
                },
            )
        )
        self._publish_failure()

    def _try_record(self, action):
        try:
            action()
        except Exception as error:  # noqa: BLE001 -- preserve recording failure in memory
            detail = f"{type(error).__name__}:{error}"
            if (
                detail not in self.continuation_errors
                and len(self.continuation_errors) < 32
            ):
                self.continuation_errors.append(detail)

    def _publish_failure(self):
        # This sink belongs to OperatorComponents and does not require disk I/O.
        # Never represent missing raw data or a failed scene as a valid outcome.
        record = {
            **self._last_record,
            "execution_status": "FAILED",
            "hazard_state": "FAILED",
            "occurrence_result": "INVALID",
            "scene_reason": self.last_reason,
            "session_action": "CONTINUE",
            "partial_data": True,
            "analysis_eligible": False,
            "m2_active_elapsed_s": self.elapsed,
            "cleanup": self.cleanup_result,
            "recording_errors": list(self.continuation_errors),
            "raw_storage": self._raw_storage_record(),
            "planned_exposure_active_s": self.planned,
            "first_geometry": self.first_geometry,
            "display_candidate": self.first_display,
            "occurrence_reserved": self.claimed,
            "scene_error_policy": "INVALIDATE_SCENE_CONTINUE_MODULE",
        }
        self._try_record(lambda: self.sink(record))

    def cleanup_backend(self):
        if self.cleanup_result is None:
            backend = getattr(self, "backend", None)
            try:
                self.cleanup_result = (
                    {"failures": [], "remaining_actor_ids": []}
                    if backend is None
                    else backend.cleanup()
                )
            except Exception as error:  # noqa: BLE001 -- retain cleanup failure for the operator
                self.cleanup_result = {
                    "failures": [f"{type(error).__name__}:{error}"],
                    "remaining_actor_ids": []
                    if backend is None
                    else list(backend.owned.actors),
                }
        return self.cleanup_result

    def after_control(self, driver_input, poll_started_ns, poll_completed_ns):
        if self.closed:
            return
        if self.scene_failure is None:
            try:
                super().after_control(driver_input, poll_started_ns, poll_completed_ns)
            except Exception as error:  # noqa: BLE001 -- scene failure is not session failure
                self.invalidate(error, "CONTROL")
        else:
            # One bounded pending batch is retained on a storage failure; do not
            # accumulate an unbounded stream while the disk is unavailable.
            self._try_record(
                lambda: self.capture_raw_input(
                    driver_input, poll_started_ns, poll_completed_ns
                )
            )
            self._publish_failure()

    def after_display_completed(self, frame):
        if self.scene_failure is not None or self.closed:
            return
        try:
            super().after_display_completed(frame)
        except Exception as error:  # noqa: BLE001 -- invalid visibility ends only the scene
            self.invalidate(error, "DISPLAY")

    def finalize(self, reason, error=None):
        if self._initializing:
            self.invalidate(error or RuntimeError(reason), "SETUP")
            return
        if error is not None:
            self.invalidate(error, "DRIVING_RUNTIME")
        try:
            super().finalize(reason, error or self.scene_failure)
        except Exception as failure:  # noqa: BLE001 -- do not turn cleanup into session abort
            self.invalidate(failure, "FINALIZE")
        if self.scene_failure is not None:
            self._publish_failure()
