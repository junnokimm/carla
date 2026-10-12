from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, assert_never

from src.p6.models import (
    CalibrationResult,
    ClockKind,
    DiscoveredDevice,
    EyeDatum,
    RawGazeSample,
    SourceStatus,
    Validity,
)

GazeCallback = Callable[[RawGazeSample], None]


class GazeSource(Protocol):
    @property
    def status(self) -> SourceStatus: ...
    def discover(self) -> tuple[DiscoveredDevice, ...]: ...
    def select(self, identifier: str) -> None: ...
    def calibrate(self) -> CalibrationResult: ...
    def subscribe(self, callback: GazeCallback) -> None: ...
    def unsubscribe(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RealSourceUnavailableError(RuntimeError):
    status: str

    def __str__(self) -> str:
        return self.status


def unavailable_real_source() -> GazeSource:
    raise RealSourceUnavailableError(SourceStatus.ADAPTER_UNCONFIRMED.value)


def preflight_source(source: GazeSource) -> CalibrationResult:
    match source.status:
        case SourceStatus.READY:
            pass
        case (
            SourceStatus.DISCONNECTED
            | SourceStatus.SDK_ABSENT
            | SourceStatus.ADAPTER_UNCONFIRMED
            | SourceStatus.CALIBRATION_FAILED
        ) as status:
            raise RealSourceUnavailableError(status.value)
        case unreachable:
            assert_never(unreachable)
    devices = source.discover()
    if not devices or not devices[0].connected:
        raise RealSourceUnavailableError(SourceStatus.DISCONNECTED.value)
    source.select(devices[0].identifier)
    calibration = source.calibrate()
    if calibration.status == "CALIBRATION_FAILED":
        raise RealSourceUnavailableError(SourceStatus.CALIBRATION_FAILED.value)
    return calibration


class SyntheticGazeSource:
    def __init__(
        self,
        sample_hz: float = 60.0,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._sample_hz = sample_hz
        self._monotonic_ns = monotonic_ns
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.READY

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        return (DiscoveredDevice("synthetic-1", "SYNTHETIC", True),)

    def select(self, identifier: str) -> None:
        if identifier != "synthetic-1":
            raise RealSourceUnavailableError(SourceStatus.DISCONNECTED.value)

    def calibrate(self) -> CalibrationResult:
        return CalibrationResult("SDK_SUCCESS_SYNTHETIC", False, "synthetic data only")

    def subscribe(self, callback: GazeCallback) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(callback,), name="p6-synthetic-gaze", daemon=True
        )
        self._thread.start()

    def unsubscribe(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=2.0)
        self._thread = None

    def emit(self, sequence: int) -> RawGazeSample:
        eye = EyeDatum(0.5, 0.5, Validity.VALID, None)
        return RawGazeSample(
            sequence,
            self._monotonic_ns(),
            "ns",
            ClockKind.SYSTEM,
            eye,
            eye,
            {"provider": "synthetic", "synthetic": True},
        )

    def _run(self, callback: GazeCallback) -> None:
        interval = 1.0 / self._sample_hz
        sequence = 0
        deadline = time.monotonic()
        while not self._stop.is_set():
            callback(self.emit(sequence))
            sequence += 1
            deadline += interval
            self._stop.wait(max(0.0, deadline - time.monotonic()))
