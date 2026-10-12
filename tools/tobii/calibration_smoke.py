# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: .venv-tobii/Scripts/python.exe -m tools.tobii.calibration_smoke --help
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare ETM calibration command; only --execute opens ETM")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--screen", type=int, required=True)
    parser.add_argument("--etm", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "")) /
                        "Programs/TobiiProEyeTrackerManager/TobiiProEyeTrackerManager.exe")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, help="Exclusive result JSON required with --execute")
    args = parser.parse_args(argv)
    if args.screen < 1:
        parser.error("--screen must be a positive ETM screen number")
    command = [str(args.etm), "--mode=usercalibration", f"--device-sn={args.serial}",
               f"--screen={args.screen}"]
    if not args.execute:
        print(json.dumps({"status": "NOT_RUN", "command": command, "aoi_validated": False}))
        return 0
    if args.output is None:
        parser.error("--execute requires --output")
    try:
        with args.output.open("x", encoding="utf-8") as output:
            try:
                completed = subprocess.run(command, timeout=180, check=False)
                code = completed.returncode
                status = "ETM_EXIT_OK_NOT_AOI_VALIDATED" if code == 0 else "ETM_FAILED"
            except subprocess.TimeoutExpired:
                code, status = None, "ETM_TIMEOUT"
            except OSError:
                code, status = None, "ETM_UNAVAILABLE"
            report = {"status": status, "exit_code": code, "serial_number": args.serial,
                      "screen": args.screen, "aoi_validated": False}
            output.write(json.dumps(report) + "\n")
        print(json.dumps(report))
        return 0 if code == 0 else 2
    except OSError as error:
        print(json.dumps({"status": "OUTPUT_ERROR", "detail": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
