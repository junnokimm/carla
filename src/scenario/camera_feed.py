from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Protocol

import carla


@dataclass(frozen=True, slots=True)
class CameraFeedSnapshot:
    image: carla.Image | None
    generation: int
    callback_count: int
    first_sensor_frame: int | None
    last_sensor_frame: int | None
    first_sensor_timestamp_seconds: float | None
    last_sensor_timestamp_seconds: float | None
    first_host_receipt_seconds: float | None
    last_host_receipt_seconds: float | None


class DriverViewPerformanceObserver(Protocol):
    def timestamp(self) -> float: ...

    def record_duration(self, segment: str, duration_seconds: float) -> None: ...

    def record_turn_signal_audio_duration(
        self,
        operation: str,
        duration_seconds: float,
    ) -> None: ...

    def record_camera_preparation(
        self,
        role: str,
        snapshot: CameraFeedSnapshot,
        prepared_at_seconds: float,
    ) -> None: ...


class CameraFeed:
    __slots__ = (
        "_callback_count",
        "_first_host_receipt_seconds",
        "_first_sensor_frame",
        "_first_sensor_timestamp_seconds",
        "_generation",
        "_last_host_receipt_seconds",
        "_last_sensor_frame",
        "_last_sensor_timestamp_seconds",
        "_lock",
        "latest_image",
        "receipt_clock",
        "role",
        "sensor",
    )

    def __init__(
        self,
        role: str,
        sensor: carla.Actor,
        latest_image: carla.Image | None = None,
    ) -> None:
        self.role = role
        self.sensor = sensor
        self.latest_image = latest_image
        self.receipt_clock: Callable[[], float] = perf_counter
        self._lock = Lock()
        self._generation = 0
        self._callback_count = 0
        self._first_sensor_frame: int | None = None
        self._last_sensor_frame: int | None = None
        self._first_sensor_timestamp_seconds: float | None = None
        self._last_sensor_timestamp_seconds: float | None = None
        self._first_host_receipt_seconds: float | None = None
        self._last_host_receipt_seconds: float | None = None

    def receive(self, image: carla.Image) -> None:
        received_at = self.receipt_clock()
        sensor_frame = int(image.frame)
        sensor_timestamp = float(image.timestamp)
        with self._lock:
            self.latest_image = image
            self._generation += 1
            self._callback_count += 1
            if self._first_sensor_frame is None:
                self._first_sensor_frame = sensor_frame
                self._first_sensor_timestamp_seconds = sensor_timestamp
                self._first_host_receipt_seconds = received_at
            self._last_sensor_frame = sensor_frame
            self._last_sensor_timestamp_seconds = sensor_timestamp
            self._last_host_receipt_seconds = received_at

    def snapshot(self) -> CameraFeedSnapshot:
        with self._lock:
            return CameraFeedSnapshot(
                image=self.latest_image,
                generation=self._generation,
                callback_count=self._callback_count,
                first_sensor_frame=self._first_sensor_frame,
                last_sensor_frame=self._last_sensor_frame,
                first_sensor_timestamp_seconds=self._first_sensor_timestamp_seconds,
                last_sensor_timestamp_seconds=self._last_sensor_timestamp_seconds,
                first_host_receipt_seconds=self._first_host_receipt_seconds,
                last_host_receipt_seconds=self._last_host_receipt_seconds,
            )
