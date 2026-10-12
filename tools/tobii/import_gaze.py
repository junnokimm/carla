# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# Run: .venv/Scripts/python.exe -m tools.tobii.import_gaze --help
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.p6.aoi_config import load_aoi_layout
from src.p6.clock import SessionOrigin
from src.p6.clock_evidence import read_clock_evidence
from src.p6.tobii.protocol import TobiiSampleDecoder
from src.p6.writer import GazeSidecarWriter
from tools.tobii.sdk import TobiiError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert raw smoke JSONL using the existing P6 clock/writer/AOI; no CARLA connection")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--origin-monotonic-ns", type=int)
    parser.add_argument("--origin-utc-ns", type=int)
    parser.add_argument("--aoi", type=Path)
    args = parser.parse_args(argv)
    if (args.origin_monotonic_ns is None) != (args.origin_utc_ns is None):
        parser.error("Supply both research-session origin values or neither")
    try:
        source_status = "NO_TERMINAL_STATUS"
        smoke_origin: SessionOrigin | None = None
        with args.input.open(encoding="utf-8") as source:
            for line in source:
                record = json.loads(line)
                if record.get("record_type") == "status":
                    source_status = record["status"]
                elif record.get("record_type") == "session_origin":
                    smoke_origin = SessionOrigin(record["monotonic_ns"], record["utc_ns"])
        origin = (smoke_origin if args.origin_monotonic_ns is None else
                  SessionOrigin(args.origin_monotonic_ns, args.origin_utc_ns))
        if origin is None:
            raise TobiiError("SESSION_ORIGIN_REQUIRED", "No smoke origin or explicit research origin")
        evidence = read_clock_evidence(args.input)
        mapping = evidence.fit(origin)
        layout = None if args.aoi is None else load_aoi_layout(args.aoi)
        writer = GazeSidecarWriter(path=args.output, run_id=args.run_id, origin=origin,
                                  mapping=mapping, layout=layout)
        count = 0
        writer.start()
        try:
            decoder = TobiiSampleDecoder()
            with args.input.open(encoding="utf-8") as source:
                for line in source:
                    if json.loads(line).get("record_type") != "gaze":
                        continue
                    envelope = decoder.decode(line)
                    writer.capture(envelope.sample, receive_monotonic_ns=envelope.host_monotonic_ns,
                                   receive_utc_ns=envelope.host_wall_time_ns)
                    count += 1
        finally:
            writer.close()
        if writer.worker_error is not None:
            raise TobiiError("WRITER_ERROR", str(writer.worker_error))
        print(json.dumps({"status": "CONVERTED" if count else "NO_SAMPLES",
                          "samples": count, "source_status": source_status,
                          "software_clock_fit": mapping.fitted, "timing_quality_verified": mapping.verified,
                          "clock_mapping_reason": mapping.provenance.reason, "clock_evidence": evidence.report(),
                          "aoi_validated": False, "hardware_validated": False}))
        return 0 if count and source_status == "RAW_CAPTURED" else 2
    except (OSError, ValueError, KeyError, TypeError, TobiiError) as error:
        print(json.dumps({"status": "IMPORT_ERROR", "detail": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
