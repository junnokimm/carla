from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from threading import Lock
from time import perf_counter

import carla

from src.experiment.automation import DrivingControlMode
from src.scenario.camera_feed import CameraFeed, CameraFeedSnapshot
from src.scenario.driver_view import DriverViewConfig
from src.scenario.research_camera_metadata import (
    UNKNOWN,
    JsonValue,
    ResearchCameraRunMeasurements,
    _clock_payload,
    _environment_payload,
    _git_state,
    _requested_sensor_payload,
    _run_measurements_payload,
    _sensor_payload,
)
from src.scenario.research_turn_signal_audio_diagnostics import TurnSignalAudioTiming

# Fixed-size correlation of existing spans plus previously unmeasured loop hooks.
# Nested camera/audio timings are deliberately not summed as separate loop costs.
LOOP_CORRELATION_SEGMENTS = frozenset(
    {
        "event_poll_and_presentation",
        "mode_events",
        "input_collection",
        "input_observer",
        "scheduler",
        "manual_control_preparation",
        "manual_control_delivery",
        "post_control",
        "audio",
        "frame_prepare",
        "draw",
        "display_flip",
        "after_display_flip",
        "fps_limiter",
    }
)


class _RunningStats:
    __slots__ = ("count", "maximum", "total")

    def __init__(self) -> None:
        self.count = 0
        self.maximum = 0.0
        self.total = 0.0

    def record(self, value: float) -> None:
        self.count += 1
        self.maximum = max(self.maximum, value)
        self.total += value

    def payload(self) -> dict[str, int | float]:
        mean = 0.0 if self.count == 0 else self.total / self.count
        return {
            "count": self.count,
            "mean_ms": _rounded_ms(mean),
            "max_ms": _rounded_ms(self.maximum),
            "total_ms": _rounded_ms(self.total),
        }


class _RoleRenderState:
    __slots__ = (
        "age",
        "last_identity",
        "latest_age",
        "no_image_count",
        "prepared_count",
        "repeated_count",
        "updated_count",
    )

    def __init__(self) -> None:
        self.prepared_count = 0
        self.updated_count = 0
        self.repeated_count = 0
        self.no_image_count = 0
        self.last_identity: tuple[str, int, float | None] | None = None
        self.latest_age: float | None = None
        self.age = _RunningStats()


class _RoleCallbackState:
    __slots__ = (
        "callback_duration",
        "count",
        "first_sensor_frame",
        "first_sensor_timestamp",
        "host_arrival_interval",
        "last_host_receipt",
        "last_sensor_frame",
        "last_sensor_timestamp",
    )

    def __init__(self) -> None:
        self.count = 0
        self.first_sensor_frame: int | None = None
        self.last_sensor_frame: int | None = None
        self.first_sensor_timestamp: float | None = None
        self.last_sensor_timestamp: float | None = None
        self.last_host_receipt: float | None = None
        self.host_arrival_interval = _RunningStats()
        self.callback_duration = _RunningStats()

    def record(
        self,
        sensor_frame: int,
        sensor_timestamp: float,
        host_receipt: float,
        duration: float,
    ) -> None:
        if self.first_sensor_frame is None:
            self.first_sensor_frame = sensor_frame
            self.first_sensor_timestamp = sensor_timestamp
        if self.last_host_receipt is not None:
            self.host_arrival_interval.record(host_receipt - self.last_host_receipt)
        self.last_sensor_frame = sensor_frame
        self.last_sensor_timestamp = sensor_timestamp
        self.last_host_receipt = host_receipt
        self.callback_duration.record(duration)
        self.count += 1


class ResearchCameraDiagnostics:
    def __init__(
        self,
        clock: Callable[[], float] = perf_counter,
        run_id: str | None = None,
        composition: str = "front_left_right_3_rgb",
        control_mode_source: Callable[[], DrivingControlMode] | None = None,
    ) -> None:
        self._clock = clock
        self._uses_default_clock = clock is perf_counter
        self._run_id = run_id
        self._composition = composition
        self._lock = Lock()
        self._roles: dict[str, _RoleRenderState] = {}
        self._timing: dict[str, _RunningStats] = {}
        self._control_mode_source = control_mode_source
        self._timing_by_mode: dict[str, dict[str, _RunningStats]] = {}
        self._turn_signal_audio_timing = TurnSignalAudioTiming()
        self._active_callbacks: dict[str, _RoleCallbackState] = {}
        self._active_start: float | None = None
        self._active_end: float | None = None
        self._active_complete: bool | None = None
        self._completed_display_flips = 0
        self._active_completed_display_flip_baseline = 0
        self._measurement_frozen = False
        self._loop_segments: dict[str, _RunningStats] | None = None
        self._correlated_loop_count = 0
        self._slowest_loop_seconds = -1.0
        self._slowest_loop: dict[str, JsonValue] | None = None
        self._frozen_feed_snapshots: dict[str, CameraFeedSnapshot] | None = None
        self._window_start: float | None = None
        self._window_end: float | None = None
        self._environment: dict[str, JsonValue] = {
            "map_name": UNKNOWN,
        }
        self._git_head, self._git_dirty = _git_state()

    def timestamp(self) -> float:
        return self._clock()

    def capture_environment(self, world: carla.World) -> None:
        self._environment = _environment_payload(world)

    def record_duration(self, segment: str, duration_seconds: float) -> None:
        if self._measurement_frozen:
            return
        mode = (
            None if self._control_mode_source is None else self._control_mode_source()
        )
        with self._lock:
            self._timing.setdefault(segment, _RunningStats()).record(duration_seconds)
            if mode is not None:
                self._timing_by_mode.setdefault(mode.value, {}).setdefault(
                    segment, _RunningStats()
                ).record(duration_seconds)
            if self._loop_segments is not None:
                if segment in LOOP_CORRELATION_SEGMENTS:
                    self._loop_segments.setdefault(segment, _RunningStats()).record(
                        duration_seconds
                    )
                elif segment == "driver_loop":
                    self._correlated_loop_count += 1
                    if duration_seconds > self._slowest_loop_seconds:
                        self._slowest_loop_seconds = duration_seconds
                        self._slowest_loop = {
                            "completed_loop_index": self._correlated_loop_count,
                            "duration_ms": duration_seconds * 1000.0,
                            "mode_at_completion": None if mode is None else mode.value,
                            "segments": {
                                name: stats.payload()
                                for name, stats in self._loop_segments.items()
                            },
                        }
                    self._loop_segments = None

    def begin_driver_loop(self) -> None:
        with self._lock:
            if (
                not self._measurement_frozen
                and self._active_start is not None
                and self._active_end is None
            ):
                self._loop_segments = {}

    def begin_active_window(self, feeds: Sequence[CameraFeed]) -> None:
        del feeds
        with self._lock:
            self._active_callbacks = {}
            self._active_start = self._clock()
            self._active_end = None
            self._active_complete = None
            self._loop_segments = None
            self._correlated_loop_count = 0
            self._slowest_loop_seconds = -1.0
            self._slowest_loop = None
            self._active_completed_display_flip_baseline = self._completed_display_flips

    def end_active_window(
        self,
        feeds: Sequence[CameraFeed],
        *,
        complete: bool,
    ) -> None:
        del feeds
        with self._lock:
            if self._measurement_frozen:
                return
            self._active_end = self._clock()
            self._active_complete = complete
            self._loop_segments = None

    def freeze_measurement_window(self, feeds: Sequence[CameraFeed]) -> None:
        with self._lock:
            self._active_end = self._clock()
            self._active_complete = True
            self._frozen_feed_snapshots = {feed.role: feed.snapshot() for feed in feeds}
            self._measurement_frozen = True
            self._loop_segments = None

    def record_camera_callback(
        self,
        role: str,
        sensor_frame: int,
        sensor_timestamp_seconds: float,
        host_receipt_seconds: float,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            if (
                self._active_start is None
                or host_receipt_seconds < self._active_start
                or (
                    self._active_end is not None
                    and host_receipt_seconds >= self._active_end
                )
            ):
                return
            self._active_callbacks.setdefault(role, _RoleCallbackState()).record(
                sensor_frame,
                sensor_timestamp_seconds,
                host_receipt_seconds,
                duration_seconds,
            )

    def record_completed_display_flip(self) -> None:
        if self._measurement_frozen:
            return
        with self._lock:
            self._completed_display_flips += 1

    def record_turn_signal_audio_duration(
        self,
        operation: str,
        duration_seconds: float,
    ) -> None:
        if self._measurement_frozen:
            return
        with self._lock:
            self._turn_signal_audio_timing.record(operation, duration_seconds)
        if operation == "light_state_rpc":
            self.record_duration("turn_signal_audio_light_rpc", duration_seconds)

    def record_camera_preparation(
        self,
        role: str,
        snapshot: CameraFeedSnapshot,
        prepared_at_seconds: float,
    ) -> None:
        if self._measurement_frozen:
            return
        with self._lock:
            state = self._roles.setdefault(role, _RoleRenderState())
            state.prepared_count += 1
            self._window_start = (
                prepared_at_seconds
                if self._window_start is None
                else min(self._window_start, prepared_at_seconds)
            )
            self._window_end = prepared_at_seconds
            if snapshot.image is None:
                state.no_image_count += 1
                return
            identity = (
                (
                    "sensor",
                    snapshot.last_sensor_frame,
                    snapshot.last_sensor_timestamp_seconds,
                )
                if snapshot.last_sensor_frame is not None
                and snapshot.last_sensor_timestamp_seconds is not None
                else ("generation", snapshot.generation, None)
            )
            if identity != state.last_identity:
                state.updated_count += 1
                state.last_identity = identity
            else:
                state.repeated_count += 1
            if snapshot.last_host_receipt_seconds is not None:
                age = max(
                    0.0,
                    prepared_at_seconds - snapshot.last_host_receipt_seconds,
                )
                state.latest_age = age
                state.age.record(age)

    def to_json(
        self,
        feeds: Sequence[CameraFeed],
        config: DriverViewConfig | None = None,
        run_measurements: ResearchCameraRunMeasurements | None = None,
    ) -> str:
        frozen = self._frozen_feed_snapshots
        feed_snapshots = [
            (feed, feed.snapshot() if frozen is None else frozen[feed.role])
            for feed in feeds
        ]
        summary_at = self._active_end if self._measurement_frozen else self._clock()
        assert summary_at is not None
        roles: dict[str, JsonValue] = {}
        for feed, snapshot in feed_snapshots:
            role_payload = self._role_payload(feed.role, snapshot, summary_at)
            role_payload["sensor"] = _sensor_payload(
                feed.sensor,
                _requested_sensor_payload(config, feed.role),
            )
            roles[feed.role] = role_payload
        payload = {
            "schema_version": 1,
            "sensor_count": len(feeds),
            "summary_host_monotonic_seconds": _rounded(summary_at),
            "run_measurements": _run_measurements_payload(run_measurements),
            "run": {
                "run_id": self._run_id,
                "git_head": self._git_head,
                "git_dirty": self._git_dirty,
                "composition": self._composition,
                "initial_view": "DRIVER",
            },
            "environment": self._environment,
            "measurement_window": {
                "start_host_monotonic_seconds": _rounded(self._window_start),
                "end_host_monotonic_seconds": _rounded(self._window_end),
                "definition": "first through last render preparation",
            },
            "measurement_lifecycle": {
                "callback_counts": {
                    "start": "sensor attachment and listen registration",
                    "end": "summary snapshot",
                    "definition": "every camera callback received during that lifecycle",
                },
                "render_preparations": {
                    "start": "first render preparation",
                    "end": "last render preparation",
                    "definition": "prepared, updated, repeated, and no-image counters",
                },
            },
            "active_drive_window": self._active_window_payload(),
            "clocks": _clock_payload(self._uses_default_clock),
            "roles": roles,
            "timing": {
                name: stats.payload() for name, stats in sorted(self._timing.items())
            },
            "timing_by_mode": {
                mode: {name: stats.payload() for name, stats in timings.items()}
                for mode, timings in self._timing_by_mode.items()
            },
            "timing_mode_definition": "canonical mode at operation completion",
            "slowest_completed_loop": self._slowest_loop,
            "loop_correlation": {
                "completed_loop_count": self._correlated_loop_count,
                "scope": "active viewer window; incomplete/aborted loops excluded",
                "storage": "one slowest loop and one pending loop; fixed segment names",
                "interpretation": (
                    "same-loop spans, not independent maxima; absent spans are unmeasured; "
                    "mode at completion may follow a transition within the loop; "
                    "not exhaustive or a mode-specific Display FPS measurement; "
                    "fallback input_collection may nest in manual_control_preparation"
                ),
            },
            "turn_signal_audio_timing": self._turn_signal_audio_timing.payload(),
            "turn_signal_audio_timing_definitions": (
                self._turn_signal_audio_timing.definitions()
            ),
            "timing_definitions": {
                "scheduler": "scheduler.update only",
                "audio": "turn-signal audio including fresh light RPC; optional vehicle audio when enabled",
                "event_poll_and_presentation": "pygame event.get and presentation.handle_events",
                "mode_events": "mode/view/indicator event handling, including toggle light RPCs",
                "frame_prepare": "pre-draw hook, including P5 preparation",
                "after_display_flip": "post-flip hook, including P5 presentation marking",
                "draw": "complete draw including camera composition and HUD",
                "conversion.<role>": "CARLA BGRA buffer to pygame surface",
                "resize.<role>": "pygame smoothscale",
                "mirror_flip.<role>": "horizontal mirror flip",
                "blit.<role>": "surface blit to display buffer",
                "display_flip": "pygame display flip only",
                "fps_limiter": "pygame Clock.tick(60) only",
                "driver_loop": "event polling through completed FPS limiter",
                "units": "count and milliseconds",
                "rounded_ms_decimal_places": 6,
            },
            "limitations": {
                "latency_scope": "host callback receipt to render preparation only",
                "sensor_to_photon_or_gpu_latency": "not measured",
                "instrumentation_overhead": "not separately measured",
                "storage": "scalar counters and summaries only",
                "rounded_zero_duration_is_zero_cost": False,
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _active_window_payload(self) -> dict[str, JsonValue]:
        elapsed = (
            None
            if self._active_start is None or self._active_end is None
            else self._active_end - self._active_start
        )
        display_count = max(
            0,
            self._completed_display_flips
            - self._active_completed_display_flip_baseline,
        )
        return {
            "status": (
                "NOT_STARTED"
                if self._active_start is None
                else "PARTIAL"
                if self._active_complete is not True
                else "COMPLETE"
            ),
            "start_host_monotonic_seconds": _rounded(self._active_start),
            "end_host_monotonic_seconds": _rounded(self._active_end),
            "host_elapsed_seconds": _rounded(elapsed),
            "completed_display_flip_count": display_count,
            "completed_display_flips_per_wall_second": _rate(display_count, elapsed),
            "roles": {
                role: _active_role_payload(state, elapsed)
                for role, state in sorted(self._active_callbacks.items())
            },
            "definition": "viewer.run entry through exit; callbacks require host receipt within [start, end); callback lifecycle is separate",
        }

    def _role_payload(
        self,
        role: str,
        snapshot: CameraFeedSnapshot,
        summary_at: float,
    ) -> dict[str, JsonValue]:
        state = self._roles.get(role, _RoleRenderState())
        summary_age = (
            None
            if snapshot.last_host_receipt_seconds is None
            else max(0.0, summary_at - snapshot.last_host_receipt_seconds)
        )
        mean_age = None if state.age.count == 0 else state.age.total / state.age.count
        max_age = None if state.age.count == 0 else state.age.maximum
        return {
            "callback_count": snapshot.callback_count,
            "first_sensor_frame": snapshot.first_sensor_frame,
            "last_sensor_frame": snapshot.last_sensor_frame,
            "first_sensor_timestamp_seconds": snapshot.first_sensor_timestamp_seconds,
            "last_sensor_timestamp_seconds": snapshot.last_sensor_timestamp_seconds,
            "first_host_receipt_seconds": snapshot.first_host_receipt_seconds,
            "last_host_receipt_seconds": snapshot.last_host_receipt_seconds,
            "prepared_count": state.prepared_count,
            "updated_count": state.updated_count,
            "repeated_count": state.repeated_count,
            "no_image_count": state.no_image_count,
            "latest_host_age_seconds": _rounded(state.latest_age),
            "mean_host_age_seconds": _rounded(mean_age),
            "max_host_age_seconds": _rounded(max_age),
            "latest_preparation_host_age_seconds": _rounded(state.latest_age),
            "mean_preparation_host_age_seconds": _rounded(mean_age),
            "max_preparation_host_age_seconds": _rounded(max_age),
            "latest_receipt_host_age_at_summary_seconds": _rounded(summary_age),
        }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 9)


def _rounded_ms(value: float) -> float:
    return round(value * 1000.0, 6)


def _rate(count: int, elapsed: float | None) -> float | None:
    if elapsed is None or elapsed <= 0.0:
        return None
    return count / elapsed


def _active_role_payload(
    state: _RoleCallbackState,
    elapsed: float | None,
) -> dict[str, JsonValue]:
    sensor_elapsed = (
        None
        if state.first_sensor_timestamp is None or state.last_sensor_timestamp is None
        else state.last_sensor_timestamp - state.first_sensor_timestamp
    )
    sensor_frame_delta = (
        0
        if state.first_sensor_frame is None or state.last_sensor_frame is None
        else state.last_sensor_frame - state.first_sensor_frame
    )
    return {
        "callback_count": state.count,
        "callbacks_per_wall_second": _rate(state.count, elapsed),
        "sensor_frame_delta": sensor_frame_delta,
        "sensor_elapsed_seconds": _rounded(sensor_elapsed),
        "sensor_frames_per_sensor_second": _rate(sensor_frame_delta, sensor_elapsed),
        "host_arrival_interval": _optional_stats_payload(state.host_arrival_interval),
        "callback_duration": _optional_stats_payload(state.callback_duration),
    }


def _optional_stats_payload(stats: _RunningStats) -> dict[str, JsonValue]:
    return {
        "count": stats.count,
        "mean_ms": (
            None if stats.count == 0 else _rounded_ms(stats.total / stats.count)
        ),
        "max_ms": None if stats.count == 0 else _rounded_ms(stats.maximum),
    }
