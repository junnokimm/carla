"""Bounded, 1 ms histogram for completed host loops; not display FPS."""

from math import ceil, isfinite


class LoopTimingDistribution:
    def __init__(self):
        self.bins = [0] * 1001  # [0,1), ... [999,1000), [1000,infinity) ms
        self.count = 0
        self.maximum_ms = 0.0

    def record(self, seconds):
        if not isfinite(seconds) or seconds < 0:
            return
        ms = seconds * 1000
        self.bins[min(1000, int(ms))] += 1
        self.count += 1
        self.maximum_ms = max(self.maximum_ms, ms)

    def _bounds(self, fraction):
        if not self.count:
            return None
        rank = ceil(self.count * fraction)
        cumulative = 0
        for index, count in enumerate(self.bins):
            cumulative += count
            if cumulative >= rank:
                upper = (
                    self.maximum_ms
                    if index == 1000
                    else min(index + 1, self.maximum_ms)
                )
                return [index, upper]
        raise AssertionError("histogram count mismatch")

    def payload(self):
        return {
            "count": self.count,
            "p95_ms_bounds": self._bounds(0.95),
            "p99_ms_bounds": self._bounds(0.99),
        }
