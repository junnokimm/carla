from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class ScheduledEvent:
    event_id: int
    trigger_time: float


class EventScheduler:
    """Generate a deterministic sequence of generic timed events."""

    def __init__(
        self,
        event_count: int,
        min_interval: float,
        max_interval: float,
        seed: int,
    ) -> None:
        if event_count < 0:
            raise ValueError("event_count must not be negative")
        if min_interval < 0:
            raise ValueError("min_interval must not be negative")
        if max_interval < min_interval:
            raise ValueError("max_interval must be at least min_interval")

        self.event_count = event_count
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.seed = seed

    def schedule(self) -> tuple[ScheduledEvent, ...]:
        random = Random(self.seed)
        trigger_time = 0.0
        events: list[ScheduledEvent] = []

        for event_id in range(1, self.event_count + 1):
            trigger_time += random.uniform(self.min_interval, self.max_interval)
            events.append(ScheduledEvent(event_id, trigger_time))

        return tuple(events)
