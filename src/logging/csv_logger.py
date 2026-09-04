from __future__ import annotations

import csv
from pathlib import Path
from typing import Final, TextIO

from src.vehicle import VehicleState

CSV_HEADER: Final = (
    "timestamp",
    "speed_kmh",
    "steering",
    "throttle",
    "brake",
    "lane_id",
    "indicator",
)


class VehicleStateCsvLogger:
    """Write VehicleState telemetry to one CSV session file."""

    def __init__(self, session_id: str, data_dir: str | Path = "data") -> None:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"vehicle_state_{session_id}.csv"
        self._file: TextIO = self.path.open("w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_HEADER)

    def write(self, state: VehicleState) -> None:
        self._writer.writerow(
            (
                state.timestamp,
                state.speed_kmh,
                state.steering,
                state.throttle,
                state.brake,
                "" if state.lane_id is None else state.lane_id,
                state.indicator,
            )
        )

    def close(self) -> None:
        self._file.close()
