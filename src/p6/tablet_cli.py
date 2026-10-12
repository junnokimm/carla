from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from src.experiment.context import Module1Condition, Module2Condition
from src.p6.clock import SessionOrigin
from src.p6.config import load_p6_config
from src.p6.server import TabletHttpServer
from src.p6.tablet import TabletProtocolError, TabletSession
from src.p6.tablet_writer import TabletEventSidecarWriter

type TabletModule = Literal["MODULE_1", "MODULE_2"]


@dataclass(frozen=True, slots=True)
class TabletServeOptions:
    config_path: str
    run_id: str
    module: TabletModule
    condition: str
    media_file: str | None
    media_version: str
    output: str | None


def serve_tablet(options: TabletServeOptions) -> int:
    config = load_p6_config(options.config_path)
    origin = SessionOrigin(time.monotonic_ns(), time.time_ns())
    output = Path(options.output or f"p6_tablet_{options.run_id}.jsonl")
    writer = TabletEventSidecarWriter(
        output, options.run_id, origin, queue_capacity=config.queue_capacity
    )
    writer.start()
    try:
        tablet = TabletSession(
            options.run_id,
            origin,
            token=config.tablet.token or "development-loopback-token",
            event_sink=writer.capture,
        )
        _configure_tablet(tablet, options, config.tablet.media_file)
        server = TabletHttpServer(
            tablet,
            static_dir=config.tablet.static_dir or Path(__file__).with_name("static"),
            media_file=(
                Path(options.media_file)
                if options.media_file is not None
                else config.tablet.media_file
            ),
            bind=config.tablet.bind,
            port=config.tablet.port,
        )
        server.start()
        print(
            json.dumps(
                {
                    "status": "SERVING",
                    "bind": config.tablet.bind,
                    "port": server.port,
                    "url_secret": (
                        f"http://{config.tablet.bind}:{server.port}/"
                        f"#token={tablet.token}"
                    ),
                    "tablet_events": str(output),
                    "real_tablet_validated": False,
                    "real_video_validated": False,
                }
            ),
            flush=True,
        )
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
        finally:
            server.close()
    finally:
        writer.close()


def _configure_tablet(
    tablet: TabletSession, options: TabletServeOptions, configured_file: Path | None
) -> None:
    match options.module:
        case "MODULE_1":
            if options.condition not in {item.value for item in Module1Condition}:
                raise TabletProtocolError("invalid Module 1 condition")
            condition = Module1Condition(options.condition)
            tablet.configure_module_one(condition)
            if condition is Module1Condition.SURT:
                trial = tablet.prepare()
                tablet.request_start(trial.trial_uuid)
        case "MODULE_2":
            if options.condition not in {item.value for item in Module2Condition}:
                raise TabletProtocolError("invalid Module 2 condition")
            media_file = options.media_file or (
                None if configured_file is None else str(configured_file)
            )
            if media_file is None:
                raise TabletProtocolError("MEDIA_MISSING")
            tablet.configure_module_two(
                Module2Condition(options.condition), media_file, options.media_version
            )
            trial = tablet.prepare()
            tablet.request_start(trial.trial_uuid)
        case unreachable:
            assert_never(unreachable)
