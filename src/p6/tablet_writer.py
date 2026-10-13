from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

from src.p6.clock import SessionOrigin
from src.p6.tablet_models import TabletEvent


@dataclass(frozen=True, slots=True)
class TabletWriterError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


class TabletEventSidecarWriter:
    def __init__(
        self,
        path: Path,
        run_id: str,
        origin: SessionOrigin,
        *,
        queue_capacity: int = 2048,
    ) -> None:
        self.path = path
        self._run_id = run_id
        self._origin = origin
        self._queue: queue.Queue[TabletEvent | None] = queue.Queue(queue_capacity)
        self._file: TextIO | None = None
        self._thread: threading.Thread | None = None
        self._worker_error: OSError | None = None

    @property
    def worker_error(self) -> OSError | None:
        return self._worker_error

    def start(self) -> None:
        if self._thread is not None:
            return
        self._file = self.path.open("x", encoding="utf-8", newline="\n")
        self._write(
            {
                "record_type": "metadata",
                "schema_version": 2,
                "run_id": self._run_id,
                "origin_monotonic_ns": self._origin.monotonic_ns,
                "origin_utc_ns": self._origin.utc_ns,
                "browser_time_mapping": "RECEIPT_ONLY_UNVERIFIED",
            }
        )
        self._thread = threading.Thread(
            target=self._run, name="p6-tablet-writer", daemon=True
        )
        self._thread.start()

    def capture(self, event: TabletEvent) -> None:
        if self._thread is None:
            raise TabletWriterError("tablet event writer is not started")
        self._queue.put(event)
        self._queue.join()
        if self._worker_error is not None:
            raise TabletWriterError(str(self._worker_error))

    def close(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join()
        self._thread = None
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._worker_error is not None:
            raise TabletWriterError(str(self._worker_error))

    def _run(self) -> None:
        while (event := self._queue.get()) is not None:
            try:
                self._write({"record_type": "tablet_event", **asdict(event)})
            except OSError as error:
                self._worker_error = error
            finally:
                self._queue.task_done()
            if self._worker_error is not None:
                return

    def _write(self, value: dict[str, str | int | float | bool | None]) -> None:
        if self._file is None:
            raise TabletWriterError("tablet event writer file is not open")
        self._file.write(json.dumps(value, separators=(",", ":")) + "\n")
        self._file.flush()
