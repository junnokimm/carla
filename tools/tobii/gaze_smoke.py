# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: .venv-tobii/Scripts/python.exe -m tools.tobii.gaze_smoke --help
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from tools.tobii.clock_evidence import clock_metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="10-20 second raw gaze smoke; does not calibrate or control CARLA")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--seconds", type=int, choices=range(10, 21), default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    status = "WORKER_ERROR"
    completed_ok = False
    try:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps({"record_type": "session_origin",
                                     "monotonic_ns": time.monotonic_ns(),
                                     "utc_ns": time.time_ns(),
                                     "session_clock_info_json": json.dumps(clock_metadata()),
                                     "scope": "DEVELOPMENT_SMOKE"}) + "\n")
            output.flush()
            try:
                result = subprocess.run(
                    [args.python, "-B", "-m", "tools.tobii.worker", "gaze",
                     "--serial", args.serial, "--seconds", str(args.seconds)],
                    cwd=Path(__file__).resolve().parents[2], stdout=output,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    timeout=args.seconds + 10, check=False,
                )
                status = "WORKER_ERROR" if result.returncode else "RAW_CAPTURED"
                completed_ok = result.returncode == 0
            except subprocess.TimeoutExpired:
                status = "TIMEOUT"
                output.write(json.dumps({"record_type": "status", "status": status}) + "\n")
        samples = 0
        with args.output.open(encoding="utf-8") as saved:
            for line in saved:
                record = json.loads(line)
                samples += record.get("record_type") == "gaze"
                if record.get("record_type") == "status":
                    status = record["status"]
        if status == "RAW_CAPTURED" and samples == 0:
            status = "NO_SAMPLES"
        if status == "RAW_CAPTURED" and not completed_ok:
            status = "WORKER_ERROR"
        print(json.dumps({"status": status, "sample_count": samples,
                          "output": str(args.output), "calibration_status": "NOT_VERIFIED",
                          "aoi_validated": False}))
        return 0 if status == "RAW_CAPTURED" and samples > 0 else 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "SMOKE_ERROR", "detail": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
