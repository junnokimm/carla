from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from src.config import DATA_DIR
from src.logging.csv_logger import VehicleStateCsvLogger
from src.vehicle import CarlaVehicleClient, VehicleClient, VehicleState


@dataclass(frozen=True, slots=True)
class LiveLoggingConfig:
    duration: float = 10.0
    sample_interval: float = 0.1

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError("duration must be positive")
        if self.sample_interval <= 0:
            raise ValueError("sample_interval must be positive")


@dataclass(frozen=True, slots=True)
class LiveLoggingResult:
    sample_count: int
    interrupted: bool


class VehicleStateWriter(Protocol):
    def write(self, state: VehicleState) -> None: ...

    def close(self) -> None: ...


def run_logging_session(
    vehicle_client: VehicleClient,
    logger: VehicleStateWriter,
    config: LiveLoggingConfig,
) -> LiveLoggingResult:
    started_at = time.monotonic()
    sample_count = 0
    interrupted = False
    try:
        while time.monotonic() - started_at < config.duration:
            logger.write(vehicle_client.get_state())
            sample_count += 1
            time.sleep(config.sample_interval)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        logger.close()
    return LiveLoggingResult(sample_count=sample_count, interrupted=interrupted)


def parse_arguments(
    argv: Sequence[str] | None = None,
) -> tuple[LiveLoggingConfig, str | None]:
    parser = argparse.ArgumentParser(
        description="Log live CARLA hero vehicle telemetry."
    )
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--sample-interval", type=float, default=0.1)
    parser.add_argument("--session-id")
    arguments = parser.parse_args(argv)
    return (
        LiveLoggingConfig(arguments.duration, arguments.sample_interval),
        arguments.session_id,
    )


def main(argv: Sequence[str] | None = None) -> None:
    config, supplied_session_id = parse_arguments(argv)
    session_id = supplied_session_id or datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    logger = VehicleStateCsvLogger(session_id, DATA_DIR)
    print(f"Logging hero vehicle state to {logger.path}")
    result = run_logging_session(CarlaVehicleClient(), logger, config)
    if result.interrupted:
        print(f"Session interrupted after {result.sample_count} samples.")
        return
    print("Connected hero vehicle.")
    print(f"Wrote {result.sample_count} samples. Session completed cleanly.")


if __name__ == "__main__":
    main()
