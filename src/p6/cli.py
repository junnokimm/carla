from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import assert_never

from src.p6.aoi_config import load_aoi_layout
from src.p6.clock import AffineClockMapping, ClockPair, SessionOrigin
from src.p6.config import P6ConfigError, load_p6_config
from src.p6.source import RealSourceUnavailableError, SyntheticGazeSource
from src.p6.tablet_cli import TabletServeOptions, serve_tablet
from src.p6.tablet_error import TabletProtocolError
from src.p6.tablet_writer import TabletWriterError
from src.p6.writer import GazeSidecarWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6 non-driving measurement and tablet tools"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
    synthetic = commands.add_parser("synthetic")
    synthetic.add_argument("--output", required=True)
    synthetic.add_argument("--run-id", required=True)
    synthetic.add_argument("--samples", type=int, default=60)
    synthetic.add_argument("--sample-hz", type=float, default=60.0)
    serve = commands.add_parser("serve")
    serve.add_argument("--config", required=True)
    serve.add_argument("--run-id", required=True)
    serve.add_argument("--module", choices=("MODULE_1", "MODULE_2"), default="MODULE_1")
    serve.add_argument("--condition", default="SURT")
    serve.add_argument("--media-file")
    serve.add_argument("--media-version", default="development-unverified")
    serve.add_argument("--output")
    guide = commands.add_parser("calibration-guide")
    guide.add_argument("--execute-delays", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        match arguments.command:
            case "preflight":
                config = load_p6_config(arguments.config)
                if config.source_mode == "real":
                    raise RealSourceUnavailableError("BLOCKED_MODEL_SDK_UNCONFIRMED")
                source = SyntheticGazeSource(config.sample_hz)
                calibration = source.calibrate()
                print(
                    json.dumps(
                        {
                            "status": "READY_SYNTHETIC",
                            "synthetic": True,
                            "calibration": asdict(calibration),
                        }
                    )
                )
                return 0
            case "validate":
                config = load_p6_config(arguments.config)
                media = config.tablet.media_file
                layout = (
                    None
                    if config.aoi_file is None
                    else load_aoi_layout(config.aoi_file)
                )
                print(
                    json.dumps(
                        {
                            "status": "VALID",
                            "source_mode": config.source_mode,
                            "media_exists": None if media is None else media.is_file(),
                            "aoi": None
                            if layout is None
                            else asdict(layout.validate()),
                            "real_hardware_validated": False,
                        }
                    )
                )
                return 0
            case "synthetic":
                return _synthetic(
                    arguments.output,
                    arguments.run_id,
                    arguments.samples,
                    arguments.sample_hz,
                )
            case "serve":
                return serve_tablet(
                    TabletServeOptions(
                        config_path=arguments.config,
                        run_id=arguments.run_id,
                        module=arguments.module,
                        condition=arguments.condition,
                        media_file=arguments.media_file,
                        media_version=arguments.media_version,
                        output=arguments.output,
                    )
                )
            case "calibration-guide":
                return _calibration_guide(arguments.execute_delays)
            case unreachable:
                assert_never(unreachable)
    except RealSourceUnavailableError as error:
        print(json.dumps({"status": error.status, "real_hardware_validated": False}))
        return 2
    except (
        P6ConfigError,
        TabletProtocolError,
        TabletWriterError,
        FileExistsError,
    ) as error:
        print(json.dumps({"status": "ERROR", "detail": str(error)}))
        return 2


def _synthetic(output: str, run_id: str, samples: int, sample_hz: float) -> int:
    if samples <= 0 or sample_hz <= 0:
        raise P6ConfigError("samples and sample-hz must be positive")
    origin = SessionOrigin(time.monotonic_ns(), time.time_ns())
    pairs = tuple(
        ClockPair(origin.monotonic_ns + index, origin.monotonic_ns + index)
        for index in range(5)
    )
    writer = GazeSidecarWriter(
        path=Path(output),
        run_id=run_id,
        origin=origin,
        mapping=AffineClockMapping.fit("ns", pairs, origin),
        layout=None,
    )
    source = SyntheticGazeSource(sample_hz)
    writer.start()
    for sequence in range(samples):
        writer.capture(
            source.emit(sequence),
            receive_monotonic_ns=time.monotonic_ns(),
            receive_utc_ns=time.time_ns(),
        )
    writer.close()
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "synthetic": True,
                "samples_requested": samples,
                "output": output,
            }
        )
    )
    return 0


def _calibration_guide(execute_delays: bool) -> int:
    steps = ("FORWARD", "REAR_MIRROR", "LEFT_MIRROR", "RIGHT_MIRROR", "TABLET")
    for index, aoi in enumerate(steps, start=1):
        print(json.dumps({"kind": "AOI_PROMPT", "aoi": aoi, "seconds": 2}))
        if execute_delays:
            time.sleep(2.0)
        print(json.dumps({"kind": "SYNC_MARK_REQUEST", "repeat": index, "total": 5}))
    print(
        json.dumps(
            {
                "status": "NOT_VALIDATED",
                "reason": "requires confirmed hardware and measured AOI mapping",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
