"""P8 disk-write failures are data loss, not a request to stop the vehicle.

Identity/clock/programming errors still surface. Keep a small in-memory ledger
of lifecycle/state events so a disk error cannot prevent module progression.
"""

import csv

from src.logging.csv_logger import RESEARCH_EVENT_HEADER, ResearchCsvLogger
from src.p8.collection_io import notice

_ESSENTIAL = {
    "initial_state",
    "stage_start",
    "stage_end",
    "auto_state",
    "disengagement",
}


class NonfatalResearchCsvLogger(ResearchCsvLogger):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.collection_errors = []
        self.collection_failure_count = 0
        self._event_index = 0
        self._essential_rows = []

    def _failure(self, kind, error):
        self.collection_failure_count += 1
        detail = f"{kind}:{type(error).__name__}:{error}"
        if len(self.collection_errors) < 32 and detail not in self.collection_errors:
            self.collection_errors.append(detail)
            notice(detail)

    def write_event(self, segment, timestamp, event):
        values = self._common_values(segment, timestamp) + (
            event.event_type,
            "" if event.event_value is None else event.event_value,
        )
        if event.event_type in _ESSENTIAL:
            self._essential_rows.append(
                (self._event_index, dict(zip(RESEARCH_EVENT_HEADER, values)))
            )
            if len(self._essential_rows) > 16384:
                self._essential_rows.pop(0)
                self._failure(
                    "memory_ledger", RuntimeError("LIFECYCLE_LEDGER_CAPACITY")
                )
        self._event_index += 1
        try:
            self._event_writer.writerow(values)
            self._event_file.flush()
        except (OSError, csv.Error) as error:
            self._failure("event_write", error)

    def write_telemetry(self, segment, timestamp, state):
        try:
            super().write_telemetry(segment, timestamp, state)
        except (OSError, csv.Error) as error:
            self._failure("telemetry_write", error)

    @property
    def event_count(self):
        return self._event_index

    def fallback_rows(self, offset):
        return [row for index, row in self._essential_rows if index >= offset]

    def close(self):
        for stream in (self._telemetry_file, self._event_file):
            try:
                stream.close()
            except OSError as error:
                self._failure("close", error)
