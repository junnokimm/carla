# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# Run from repository root using the existing CARLA .venv: python -m tools.p7_smoke --help
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.experiment.context import ExperimentModule
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.p7.config import load_config
from src.p7.session import DevelopmentSession
from src.p7.types import EventKind, Json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P7 DEV_ONLY traffic/hazard; dry by default; never owns ego controls")
    parser.add_argument("--config", type=Path, default=Path("config/p7_dev_static_smoke.json"))
    parser.add_argument("--mode", choices=("dry", "catalog", "live"), default="dry")
    parser.add_argument("--module", choices=("MODULE_1", "MODULE_2"), default="MODULE_2")
    parser.add_argument("--ack-dev-only", action="store_true")
    parser.add_argument("--ego-id", type=int, help="Explicit ego; catalog also reports corridor compatibility/first failure without mutation")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--duration", type=int, choices=range(1, 21), default=20, metavar="1..20")
    parser.add_argument("--output-root", type=Path, default=Path("data/p7_development"))
    parser.add_argument("--occurrences", type=Path, default=Path("data/p7_occurrences"))
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    args = parser.parse_args(argv)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", args.run_id) is None:
        parser.error("--run-id must be path-safe")
    if args.mode == "live" and (not args.ack_dev_only or args.ego_id is None):
        parser.error("live requires --ack-dev-only and the ID of an externally parked ego")
    session = None
    summary: dict[str, Json] = {"status": "ABORTED"}
    try:
        if args.mode == "catalog":
            from src.p7.live import catalog
            length = load_config(args.config).corridor_length_m if args.ego_id is not None else 90
            report = catalog(args.host, args.port, ego_id=args.ego_id, corridor_length_m=length)
            print(json.dumps(report))
            return 0 if args.ego_id is None or report["corridor"]["status"] == "COMPATIBLE" else 2
        config = load_config(args.config)
        origin = HostClockTimestamp(time.monotonic_ns(), time.time_ns())
        session = DevelopmentSession(args.output_root / args.run_id, config=config,
                                     module=ExperimentModule(args.module), participant_id=f"DEV-P7-{args.run_id}",
                                     origin=origin, dry=args.mode == "dry")
        if args.mode == "dry":
            from src.p7.dry import execute
            summary = execute(session, duration=args.duration, occurrences=args.occurrences)
        else:
            from src.p7.live import LiveOptions, execute
            summary = execute(session, LiveOptions(args.host, args.port, args.ego_id, args.duration, args.occurrences))
    except (RuntimeError, ValueError, OSError) as error:
        summary = {"status": "ABORTED", "reason": str(error)}
        if session is not None:
            timestamp = session.last_timestamp if session.dry else TimestampEnvelope(HostClockTimestamp(time.monotonic_ns(), time.time_ns()))
            session.emit(EventKind.ABORT, timestamp, {"reason": str(error)})
    finally:
        if session is not None:
            summary["occurrence_store"] = str(args.occurrences.resolve())
            session.close(summary)
    print(json.dumps({**summary, "output": None if session is None else str(session.directory),
                      "hazard_validated": False, "participant_run": False}, allow_nan=False))
    return 0 if summary["status"] in ("COMPLETED", "IDLE", "TRAFFIC_ONLY_COMPLETE") else 2


if __name__ == "__main__":
    raise SystemExit(main())
