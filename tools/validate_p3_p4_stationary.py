# /// script
# requires-python = ">=3.12"
# dependencies = ["carla==0.9.16", "pygame==2.6.1", "numpy"]
# ///
# Run with the laboratory interpreter: .venv/Scripts/python.exe -m tools.validate_p3_p4_stationary
from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import carla

from src.experiment.assignment import ParticipantAssignment
from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.automation_interaction_types import AutomationInitializationError
from src.experiment.context import (
    ExperimentModule,
    Module1Condition,
    Module2Condition,
    ParticipantId,
    StudyRunId,
)
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_runtime import ResearchNoARunner


def foreground_title() -> str:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(user32.GetForegroundWindow(), buffer, len(buffer))
    return buffer.value


def source_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ("src", "tests", "tools")
        for path in sorted((root / folder).rglob("*.py"))
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output = root / "data" / "p3_p4_validation" / batch_id
    output.mkdir(parents=True, exist_ok=False)
    print(f"evidence={output}", flush=True)
    hashes = source_hashes(root)
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(5.0)
    with (
        (output / "environment.txt").open("x", encoding="utf-8") as handle,
        redirect_stdout(handle), redirect_stderr(handle),
    ):
            print("python", sys.version)
            print("head", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
            print("dirty_status", subprocess.check_output(["git", "status", "--porcelain"], text=True))
            print("foreground_before", foreground_title())
            print("quality=UNKNOWN; no editor/OS/GPU settings changed")
            world = client.get_world()
            print("versions", client.get_client_version(), client.get_server_version())
            print("map", world.get_map().name)
            print("settings", world.get_settings())
            print("vehicle_count", len(world.get_actors().filter("vehicle.*")))
            start = world.get_snapshot()
            wall = time.perf_counter()
            time.sleep(2)
            end = world.get_snapshot()
            print("idle_world_hz", (end.frame - start.frame) / (time.perf_counter() - wall))
    with (output / "source_manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(hashes, handle, indent=2)
    controls = NoAControlConfig(
        longitudinal=LongitudinalControlConfig(10, 0.2, 0.1, 0.1, 0.4, 0.5, 0.02),
        lateral=LateralControlConfig(0.2, 0.5, 0.1, 0.05, 0.15),
    )
    for name, logging_on, reject in (
        ("manual_logging_on", True, False),
        ("manual_logging_off", False, False),
        ("m2_noa_start_rejected", True, True),
    ):
        run_id = f"{batch_id}-{name}"
        directory = output / name
        directory.mkdir(exist_ok=False)
        assignment = ParticipantAssignment(
            ParticipantId("DEV-P3P4"), Module1Condition.NO_SURT,
            Module2Condition.NOA_L2 if reject else Module2Condition.MANUAL,
            "Town04-spawn0-stationary", "development-no-traffic",
            "not-validated", "HEAD-dirty-see-source-manifest",
        )
        persistence = ResearchPersistenceConfig(
            assignment, StudyRunId(run_id), directory,
            ExperimentModule.MODULE_2, f"development/{name}",
        ) if logging_on else None
        config = ResearchNoARunConfig(
            duration=6.0, control_config=controls, mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0, front_camera_only=True, camera_diagnostics=True,
            run_id=run_id,
            automation_interaction=ResearchAutomationInteractionConfig(
                ExperimentModule.MODULE_2, assignment.m2_condition,
                AutomationInteractionConfig(0.2, 0.1, 0.05, True),
                initial_lane_change_in_progress=reject,
                initial_stage=f"development/{name}",
            ),
            persistence=persistence,
        )
        runner = ResearchNoARunner(
            config, client=client,
            driver_input_source=lambda: DriverInput(brake=0.5),
        )
        with (
            (directory / "stdout.txt").open("x", encoding="utf-8") as handle,
            redirect_stdout(handle), redirect_stderr(handle),
        ):
                print("scripted_input=constant_brake_0.5; physical_controls=UNVERIFIED")
                print("injected_lane_change_flag", reject)
                print("config", config)
                try:
                    with runner.session() as session:
                        report = session.run()
                        if report is not None:
                            print(report.format())
                        if session.live_scheduler is not None:
                            for operation in (
                                "input_observer", "post_control", "snapshot_getter",
                                "control_getter", "transform_getter", "map_getter",
                                "light_getter", "telemetry_write", "event_write",
                            ):
                                print(operation, asdict(session.live_scheduler.diagnostics.operation_timing(operation)))
                        print("foreground_after_run", foreground_title())
                except AutomationInitializationError as error:
                    if not reject:
                        raise
                    print("expected_start_failure", type(error).__name__, str(error))
                except Exception:
                    traceback.print_exc()
                    raise
                print("vehicles_after_cleanup", len(client.get_world().get_actors().filter("vehicle.*")))
                if runner.log_paths is not None:
                    for path in (runner.log_paths.telemetry, runner.log_paths.events):
                        with path.open(newline="") as csv_handle:
                            rows = list(csv.DictReader(csv_handle))
                        print("csv", path.name, "rows", len(rows))
                        print("first", rows[:1], "last", rows[-1:])
        print(f"completed={name}", flush=True)
    with (output / "source_unchanged.txt").open("x", encoding="utf-8") as handle:
        print(source_hashes(root) == hashes, file=handle)
    print("Active driving intentionally not attempted in the observed low-cadence environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
