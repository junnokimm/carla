from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from threading import Lock
from time import perf_counter

import carla

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


class ResearchCameraDiagnostics:
    def __init__(
        self,
        clock: Callable[[], float] = perf_counter,
        run_id: str | None = None,
        composition: str = "front_left_right_3_rgb",
    ) -> None:
        self._clock = clock
        self._uses_default_clock = clock is perf_counter
        self._run_id = run_id
        self._composition = composition
        self._lock = Lock()
        self._roles: dict[str, _RoleRenderState] = {}
        self._timing: dict[str, _RunningStats] = {}
        self._turn_signal_audio_timing = TurnSignalAudioTiming()
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
        with self._lock:
            self._timing.setdefault(segment, _RunningStats()).record(duration_seconds)

    def record_turn_signal_audio_duration(
        self,
        operation: str,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            self._turn_signal_audio_timing.record(operation, duration_seconds)

    def record_camera_preparation(
        self,
        role: str,
        snapshot: CameraFeedSnapshot,
        prepared_at_seconds: float,
    ) -> None:
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
        feed_snapshots = [(feed, feed.snapshot()) for feed in feeds]
        summary_at = self._clock()
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
            "clocks": _clock_payload(self._uses_default_clock),
            "roles": roles,
            "timing": {
                name: stats.payload() for name, stats in sorted(self._timing.items())
            },
            "turn_signal_audio_timing": self._turn_signal_audio_timing.payload(),
            "turn_signal_audio_timing_definitions": (
                self._turn_signal_audio_timing.definitions()
            ),
            "timing_definitions": {
                "scheduler": "scheduler.update only",
                "audio": "turn-signal audio update only",
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
