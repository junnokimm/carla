from dataclasses import dataclass

from src.experiment.scheduler import EventScheduler
from src.logging.csv_logger import VehicleStateCsvLogger
from src.vehicle import VehicleClient


@dataclass(frozen=True)
class ExperimentSessionResult:
    sample_count: int
    triggered_event_ids: tuple[int, ...]
    completed: bool


class ExperimentSession:
    """Run one deterministic mock-compatible experiment session."""

    def __init__(
        self,
        vehicle_client: VehicleClient,
        logger: VehicleStateCsvLogger,
        scheduler: EventScheduler,
        duration: float,
    ) -> None:
        self.vehicle_client = vehicle_client
        self.logger = logger
        self.scheduler = scheduler
        self.duration = duration

    def run(self, sample_interval: float) -> ExperimentSessionResult:
        if sample_interval <= 0:
            raise ValueError("sample_interval must be positive")

        schedule = self.scheduler.schedule()
        triggered_event_ids: list[int] = []
        elapsed_time = 0.0
        event_index = 0
        sample_count = 0

        while elapsed_time <= self.duration:
            self.logger.write(self.vehicle_client.get_state())
            sample_count += 1

            while (
                event_index < len(schedule)
                and schedule[event_index].trigger_time <= elapsed_time
            ):
                triggered_event_ids.append(schedule[event_index].event_id)
                event_index += 1

            elapsed_time += sample_interval

        self.logger.close()
        return ExperimentSessionResult(
            sample_count=sample_count,
            triggered_event_ids=tuple(triggered_event_ids),
            completed=True,
        )
