from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO, assert_never

from src.p6.aoi import AoiLayout
from src.p6.aoi_config import load_aoi_layout
from src.p6.clock import AffineClockMapping, ClockPair, SessionOrigin
from src.p6.config import P6Config
from src.p6.models import RawGazeSample
from src.p6.server import TabletHttpServer
from src.p6.source import (
    GazeSource,
    SyntheticGazeSource,
    preflight_source,
    unavailable_real_source,
)
from src.p6.tablet import TabletSession
from src.p6.writer import GazeSidecarWriter


@dataclass(frozen=True, slots=True)
class P6RuntimeError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class P6Health:
    source_status: str
    calibration_status: str
    running: bool
    writer_error: str | None
    tablet_server_port: int | None


class P6Runtime:
    def __init__(
        self,
        *,
        config: P6Config,
        run_id: str,
        output_dir: Path,
        origin: SessionOrigin,
        layout: AoiLayout | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._config = config
        self._origin = origin
        self._monotonic_ns = monotonic_ns
        self._utc_ns = utc_ns
        self._running = False
        self._closed = False
        match config.source_mode:
            case "synthetic":
                source: GazeSource = SyntheticGazeSource(config.sample_hz, monotonic_ns)
            case "real":
                source = unavailable_real_source()
            case unreachable:
                assert_never(unreachable)
        calibration = preflight_source(source)
        self._source = source
        self._calibration_status = calibration.status
        pairs = tuple(
            ClockPair(origin.monotonic_ns + index, origin.monotonic_ns + index)
            for index in range(5)
        )
        mapping = (
            AffineClockMapping.fit("ns", pairs, origin)
            if config.source_mode == "synthetic"
            else AffineClockMapping.unverified("ns", origin)
        )
        active_layout = (
            load_aoi_layout(config.aoi_file)
            if layout is None and config.aoi_file is not None
            else layout
        )
        self._writer = GazeSidecarWriter(
            path=output_dir / f"p6_gaze_{run_id}.jsonl",
            run_id=run_id,
            origin=origin,
            mapping=mapping,
            layout=active_layout,
            queue_capacity=config.queue_capacity,
        )
        tablet_token = config.tablet.token or secrets.token_urlsafe(24)
        self.tablet = TabletSession(
            run_id, origin, token=tablet_token, monotonic_ns=monotonic_ns
        )
        self._tablet_path = output_dir / f"p6_tablet_{run_id}.jsonl"
        try:
            self._tablet_file: TextIO = self._tablet_path.open(
                "x", encoding="utf-8", newline="\n"
            )
        except FileExistsError:
            self._writer.close()
            raise
        self._server: TabletHttpServer | None = None

    @property
    def gaze_path(self) -> Path:
        return self._writer.path

    @property
    def tablet_path(self) -> Path:
        return self._tablet_path

    @property
    def health(self) -> P6Health:
        error = self._writer.worker_error
        return P6Health(
            self._source.status.value,
            self._calibration_status,
            self._running,
            None if error is None else f"{type(error).__name__}:{error}",
            None if self._server is None else self._server.port,
        )

    def start(self) -> None:
        if self._closed:
            raise P6RuntimeError("P6 runtime is closed")
        if self._running:
            return
        self._writer.start()
        if self._config.tablet.enabled:
            self._server = TabletHttpServer(
                self.tablet,
                static_dir=self._config.tablet.static_dir,
                media_file=self._config.tablet.media_file,
                bind=self._config.tablet.bind,
                port=self._config.tablet.port,
            )
            self._server.start()

        def capture(sample: RawGazeSample) -> None:
            self._writer.capture(
                sample,
                receive_monotonic_ns=self._monotonic_ns(),
                receive_utc_ns=self._utc_ns(),
            )

        self._source.subscribe(capture)
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._source.unsubscribe()
        self._running = False
        if self._server is not None:
            self._server.close()
        self._finalize()
        error = self._writer.worker_error
        if error is not None:
            raise P6RuntimeError(f"P6 writer failed: {type(error).__name__}:{error}")

    def close(self) -> None:
        if self._closed:
            return
        if self._running:
            self.stop()
            return
        self._finalize()

    def _finalize(self) -> None:
        if self._closed:
            return
        self.tablet.end()
        self._tablet_file.write(
            json.dumps(
                {
                    "record_type": "metadata",
                    "schema_version": 1,
                    "origin_monotonic_ns": self._origin.monotonic_ns,
                    "origin_utc_ns": self._origin.utc_ns,
                    "browser_time_mapping": "null until five verified sync pairs",
                    "real_hardware_validated": False,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        for event in self.tablet.events():
            self._tablet_file.write(
                json.dumps(
                    {"record_type": "tablet_event", **asdict(event)},
                    separators=(",", ":"),
                )
                + "\n"
            )
        self._tablet_file.flush()
        self._tablet_file.close()
        self._writer.close()
        self._closed = True
