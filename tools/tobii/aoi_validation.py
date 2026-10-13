# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# Run with the existing CARLA .venv: python -m tools.tobii.aoi_validation --help
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.p6.aoi_config import load_aoi_layout
from src.p6.tobii.client import TobiiProcessSource
from src.p6.validation.run import ValidationRun


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded real Tobii + read-only CARLA cockpit AOI evidence; no approval or participant run")
    parser.add_argument("--tobii-python", type=Path, required=True)
    parser.add_argument("--tobii-serial", required=True)
    parser.add_argument("--aoi", type=Path, required=True)
    parser.add_argument("--actor-id", type=int, help="Existing externally parked CARLA vehicle; never spawned/controlled here")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--max-seconds", type=int, choices=range(20, 181), default=120,
                        metavar="20..180", help="Total live-window deadline, including preparation (default: 120)")
    parser.add_argument("--output-root", type=Path, default=Path("data/p6_aoi_validation"))
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    parser.add_argument("--preflight-only", action="store_true", help="Only bounded Tobii discovery/selection; never connects to CARLA")
    args = parser.parse_args(argv)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", args.run_id) is None:
        parser.error("--run-id must be a safe single directory name")
    if not args.preflight_only and args.actor_id is None:
        parser.error("--actor-id is required for live preview")
    run = None
    source = TobiiProcessSource(args.tobii_python.resolve(), seconds=args.max_seconds, validation=True)
    failed = False
    try:
        run = ValidationRun(args.output_root / args.run_id, load_aoi_layout(args.aoi))
        source.record_sink = run.recorder.capture
        source.discover()
        source.select(args.tobii_serial)
        if args.preflight_only:
            run.sequence.abort(time.monotonic_ns(), "PREFLIGHT_ONLY_NO_AOI_RUN")
        else:
            from src.p6.validation.live import LiveOptions, execute

            execute(run, source, LiveOptions(args.host, args.port, args.actor_id, args.max_seconds))
    except (OSError, RuntimeError, ValueError) as error:
        failed = True
        if run is not None:
            run.errors.append(f"{type(error).__name__}:{error}")
            run.sequence.abort(time.monotonic_ns(), str(error))
        print(json.dumps({"status": "ABORTED", "reason": str(error)}, ensure_ascii=True))
    except KeyboardInterrupt:
        failed = True
        if run is not None:
            run.sequence.abort(time.monotonic_ns(), "KEYBOARD_INTERRUPT")
    finally:
        if source.running:
            source.unsubscribe()
        if run is not None:
            if source.running:
                run.errors.append("TOBII_SHUTDOWN_TIMEOUT")
            if run.sequence.phase == "DONE" and source.last_error not in ("RAW_CAPTURED", "STOPPED"):
                run.errors.append(f"TOBII_TERMINAL:{source.last_error}")
            if run.sequence.phase not in ("DONE", "ABORTED"):
                run.sequence.abort(time.monotonic_ns(), "INCOMPLETE")
            report = run.finish()
            print(json.dumps({"status": report["status"], "output": str(run.directory),
                              "aoi_validated": False, "hardware_validated": False}))
            failed = failed or bool(run.errors) or (not args.preflight_only and report["status"] != "DONE")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
