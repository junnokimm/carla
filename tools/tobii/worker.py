# /// script
# requires-python = "==3.10.*"
# dependencies = ["tobii-research==2.1.0"]
# ///
# Run from repository root: .venv-tobii/Scripts/python.exe -m tools.tobii.worker
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from dataclasses import asdict

from tools.tobii.capture import CaptureOptions, WireRecord, capture
from tools.tobii.sdk import TobiiError, discover, load_sdk

logger = logging.getLogger(__name__)


def emit(record: WireRecord) -> None:
    print(json.dumps(record, ensure_ascii=True, allow_nan=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Tobii SDK worker; no CARLA imports")
    parser.add_argument("action", choices=("discover", "gaze", "validate"))
    parser.add_argument("--serial")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args(argv)
    if args.action in ("gaze", "validate") and not args.serial:
        parser.error("gaze requires an explicit --serial from discovery")
    maximum = 180 if args.action == "validate" else 20
    if not 10 <= args.seconds <= maximum:
        parser.error(f"--seconds must be between 10 and {maximum}")
    try:
        sdk = load_sdk()
        if args.action == "discover":
            report = discover(sdk)
            print(json.dumps({"record_type": "discovery", **asdict(report),
                              "device_count": len(report.devices)}, ensure_ascii=True), flush=True)
            return 0 if report.status == "OK" else 2
        stop_requested = threading.Event()
        if args.action == "validate":
            def read_stop() -> None:
                sys.stdin.readline()
                stop_requested.set()

            threading.Thread(target=read_stop, name="tobii-validation-stop", daemon=True).start()
        result = capture(sdk, CaptureOptions(args.serial, args.seconds), emit,
                         stop_requested=stop_requested)
        return 0 if result == "RAW_CAPTURED" else 2
    except TobiiError as error:
        emit({"record_type": "status", "status": error.status, "detail": error.detail})
        return 2
    except Exception as error:
        # Native SDK errors are contained at this process boundary, never in CARLA.
        logger.exception("Tobii worker failed")
        emit({"record_type": "status", "status": "SDK_ERROR",
              "detail": f"{type(error).__name__}: {error}"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
