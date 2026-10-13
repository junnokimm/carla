from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from src.p6.models import CalibrationResult, DiscoveredDevice, SourceStatus
from src.p6.source import GazeCallback, RealSourceUnavailableError
from src.p6.tobii.protocol import TobiiSampleDecoder
from tools.tobii.sdk import TobiiError


class TobiiProcessSource:
    """Bounded development capture over private stdout IPC, never a vehicle control owner."""

    def __init__(self, python: Path, *, seconds: int = 15, validation: bool = False) -> None:
        maximum = 180 if validation else 20
        if not 10 <= seconds <= maximum:
            raise TobiiError("INVALID_DURATION", f"Use 10-{maximum} seconds for bounded capture")
        self._python = python
        self._seconds = seconds
        self._validation = validation
        self.record_sink: Callable[[str], None] | None = None
        self._serial: str | None = None
        self._devices: tuple[DiscoveredDevice, ...] = ()
        self._status = SourceStatus.DISCONNECTED
        self.last_error: str | None = "NOT_DISCOVERED"
        self.calibration_state = "NOT_VERIFIED"
        self.finished = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._root = Path(__file__).resolve().parents[3]

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _command(self, action: str) -> list[str]:
        worker_action = "validate" if action == "gaze" and self._validation else action
        command = [str(self._python), "-B", "-m", "tools.tobii.worker", worker_action]
        if action == "gaze" and self._serial is not None:
            command += ["--serial", self._serial, "--seconds", str(self._seconds)]
        return command

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        try:
            result = subprocess.run(self._command("discover"), cwd=self._root,
                                    capture_output=True, text=True, encoding="utf-8",
                                    timeout=10, check=False)
            report = json.loads(result.stdout)
            status = report["status"]
            if status != "OK" or result.returncode != 0:
                self.last_error = status
                self._status = (SourceStatus.SDK_ABSENT if status == "SDK_NOT_INSTALLED"
                                else SourceStatus.DISCONNECTED)
                raise RealSourceUnavailableError(status)
            self._devices = tuple(
                DiscoveredDevice(item["serial_number"], item["model"], True)
                for item in report["devices"] if item["model"] == "Tobii Pro Nano"
            )
            if not self._devices:
                raise RealSourceUnavailableError("NO_DEVICE")
            self._status, self.last_error = SourceStatus.READY, None
            return self._devices
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
            self._status, self.last_error = SourceStatus.DISCONNECTED, str(error)
            raise RealSourceUnavailableError("TOBII_WORKER_UNAVAILABLE") from error

    def select(self, identifier: str) -> None:
        if not any(device.identifier == identifier for device in self._devices):
            raise RealSourceUnavailableError("NO_DEVICE")
        self._serial = identifier

    def calibrate(self) -> CalibrationResult:
        raise RealSourceUnavailableError("CALIBRATION_NOT_VERIFIED_USE_ETM")

    def subscribe(self, callback: GazeCallback) -> None:
        if self._serial is None or self._status is not SourceStatus.READY:
            raise RealSourceUnavailableError("DISCOVERY_AND_SELECTION_REQUIRED")
        if self._thread is not None:
            raise RealSourceUnavailableError("ALREADY_SUBSCRIBED")
        self.finished.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive, args=(callback,),
                                        name="p6-tobii-ipc", daemon=True)
        self._thread.start()

    def unsubscribe(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            if self._validation and process.stdin is not None:
                try:
                    process.stdin.write("STOP\n")
                    process.stdin.flush()
                    process.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            if not self._thread.is_alive():
                self._thread = None
        self._status = SourceStatus.DISCONNECTED

    def _receive(self, callback: GazeCallback) -> None:
        decoder = TobiiSampleDecoder()
        terminal = False
        watchdog: threading.Timer | None = None
        try:
            with subprocess.Popen(self._command("gaze"), cwd=self._root,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  stdin=subprocess.PIPE,
                                  text=True, encoding="utf-8") as process:
                self._process = process

                def timeout() -> None:
                    self.last_error = "TIMEOUT"
                    if process.poll() is None:
                        process.kill()

                watchdog = threading.Timer(self._seconds + 10, timeout)
                watchdog.daemon = True
                watchdog.start()
                if self._stop.is_set():
                    process.kill()
                if process.stdout is None:
                    raise TobiiError("WORKER_ERROR", "Missing IPC stdout")
                try:
                    for line in process.stdout:
                        if self.record_sink is not None:
                            self.record_sink(line)
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise TobiiError("INVALID_RECORD", "JSON object required")
                        if record.get("record_type") == "gaze":
                            sample = decoder.decode(line).sample
                            if sample.source_metadata["serial_number"] != self._serial:
                                raise TobiiError("IDENTITY_MISMATCH", "Unexpected tracker")
                            callback(sample)
                        elif record.get("record_type") == "status":
                            self.last_error = record["status"]
                            terminal = True
                finally:
                    if process.poll() is None:
                        process.kill()
        except (OSError, ValueError, KeyError, TypeError, TobiiError) as error:
            self.last_error = str(error)
            terminal = True
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if not terminal and self.last_error is None:
                self.last_error = "STOPPED" if self._stop.is_set() else "WORKER_EXIT"
            self._status = SourceStatus.DISCONNECTED
            self._process = None
            self.finished.set()
