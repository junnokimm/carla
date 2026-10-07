from __future__ import annotations


class _SummaryStats:
    __slots__ = ("count", "maximum", "total")

    def __init__(self) -> None:
        self.count = 0
        self.maximum = 0.0
        self.total = 0.0

    def record(self, duration_seconds: float) -> None:
        self.count += 1
        self.maximum = max(self.maximum, duration_seconds)
        self.total += duration_seconds

    def payload(self) -> dict[str, int | float]:
        mean = 0.0 if self.count == 0 else self.total / self.count
        return {
            "count": self.count,
            "mean_ms": round(mean * 1000.0, 6),
            "max_ms": round(self.maximum * 1000.0, 6),
        }


class TurnSignalAudioTiming:
    __slots__ = ("_operations",)

    def __init__(self) -> None:
        self._operations = {
            operation: (_SummaryStats(), _SummaryStats())
            for operation in ("light_state_rpc", "audio_update")
        }

    def record(self, operation: str, duration_seconds: float) -> None:
        first, subsequent = self._operations[operation]
        (first if first.count == 0 else subsequent).record(duration_seconds)

    def payload(self) -> dict[str, dict[str, dict[str, int | float]]]:
        return {
            operation: {
                "first_call": first.payload(),
                "subsequent_calls": subsequent.payload(),
            }
            for operation, (first, subsequent) in sorted(self._operations.items())
        }

    @staticmethod
    def definitions() -> dict[str, str]:
        return {
            "light_state_rpc": "hero.get_light_state call only",
            "audio_update": "TurnSignalAudio.update call only",
            "first_call": "first observed call for each operation",
            "subsequent_calls": (
                "all observed calls after the first for each operation"
            ),
            "units": "count and milliseconds",
        }
