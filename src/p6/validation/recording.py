from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict
from pathlib import Path

from src.p6.clock import SessionOrigin
from src.p6.validation.mapping import ValidationError
from tools.tobii.clock_evidence import clock_metadata


class RawRecorder:
    def __init__(self, path: Path, origin: SessionOrigin) -> None:
        self._output = path.open("x", encoding="utf-8", newline="\n")
        self._output.write(json.dumps({"record_type": "session_origin", **asdict(origin),
                                       "session_clock_info_json": json.dumps(clock_metadata()),
                                       "scope": "AOI_VALIDATION_NOT_PARTICIPANT"}) + "\n")
        self._output.flush()
        self._queue: queue.Queue[str] = queue.Queue(2048)
        self._closing = threading.Event()
        self.error: str | None = None
        self.dropped = 0
        self._thread = threading.Thread(target=self._write, name="p6-validation-raw", daemon=True)
        self._thread.start()

    def capture(self, line: str) -> None:
        if self._closing.is_set():
            return
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            self.dropped += 1
            self.error = "VALIDATION_LOG_QUEUE_OVERFLOW"

    def close(self) -> None:
        self._closing.set()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise ValidationError("VALIDATION_LOG_SHUTDOWN_TIMEOUT")

    def _write(self) -> None:
        try:
            with self._output:
                while not self._closing.is_set() or not self._queue.empty():
                    try:
                        line = self._queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    self._output.write(line.rstrip("\r\n") + "\n")
                    self._output.flush()
        except OSError as error:
            self.error = f"VALIDATION_LOG_ERROR:{error}"
