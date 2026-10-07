from __future__ import annotations

from src.experiment.timestamp import HostClockTimestamp


class SessionClockError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class SessionClock:
    __slots__ = ("_baseline_ns", "_latest_ns")

    def __init__(self, baseline: HostClockTimestamp) -> None:
        self._baseline_ns = baseline.monotonic_ns
        self._latest_ns = baseline.monotonic_ns

    @property
    def baseline_monotonic_ns(self) -> int:
        return self._baseline_ns

    def elapsed_seconds(self, timestamp: HostClockTimestamp) -> float:
        current_ns = timestamp.monotonic_ns
        if current_ns < self._latest_ns:
            raise SessionClockError("host monotonic timestamp moved backwards")
        self._latest_ns = current_ns
        return (current_ns - self._baseline_ns) / 1_000_000_000.0
