# /// script
# requires-python = ">=3.12"
# dependencies = ["carla==0.9.16", "pygame==2.6.1", "numpy"]
# ///
# Laboratory interpreter: .venv/Scripts/python.exe -m tools.validate_p5_exit47_fix
from __future__ import annotations

import csv
import json
import subprocess
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import carla

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance import ExitAssistanceStatus
from src.scenario.research_noa_cli import parse_arguments
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.vehicle.carla_noa_simulation_control import CarlaNoAControlStep
from tools.validate_p3_p4_stationary import source_hashes
from tools.validate_p5_readonly import editor_states


def main() -> None:
    root = Path.cwd()
    run_id = "p5-exit47-fix-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    directory = root / "data" / "p5_live_validation" / run_id
    directory.mkdir(exist_ok=False)
    print("evidence", directory, flush=True)
    hashes = source_hashes(root)
    with (directory / "source_manifest.json").open("x", encoding="utf-8") as destination:
        json.dump(hashes, destination, indent=2)
    arguments = [
        "--live-smoke", "--development-validation", "--duration", "90",
        "--spawn-index", "107", "--front-camera-only", "--camera-diagnostics",
        "--run-id", run_id, "--assignment-file", "data/p5_live_dev_assignment.csv",
        "--participant-id", "DEV-P5-LIVE", "--output-dir", str(directory),
        "--automation-module", "MODULE_2", "--interaction-stage", "development/p5-exit47-fix",
        "--activation-center-tolerance-m", "0.2", "--activation-heading-tolerance-rad", "0.1",
        "--driver-brake-threshold", "0.05", "--button-deactivation", "enabled",
        "--exit-route-id", "town04-exit-47", "--exit-event-id", run_id + "-exit",
        "--target-speed-kmh", "10", "--speed-deadband-kmh", "0.2",
        "--acceleration-gain", "0.1", "--braking-gain", "0.1", "--integral-gain", "0.02",
        "--max-throttle", "0.4", "--max-brake", "0.5", "--lateral-error-gain", "0.2",
        "--heading-error-gain", "0.5", "--lateral-deadband-m", "0.1",
        "--heading-deadband-rad", "0.05", "--max-steering", "0.15",
    ]
    trace: list[tuple[int | float | str, ...]] = []
    with (directory / "stdout.txt").open("x", encoding="utf-8") as output, redirect_stdout(output), redirect_stderr(output):
        print("HEAD", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
        print("dirty", subprocess.check_output(["git", "status", "--porcelain", "--", "src", "tools", "tests", "config"], text=True))
        print("arguments", json.dumps(arguments))
        print("SCRIPTED confirmation 2.5s after observing presentation; no physical keyboard confirmation claimed; brake after completion")
        states = editor_states()
        client = carla.Client("127.0.0.1", 2000)
        client.set_timeout(5.0)
        world = client.get_world()
        initial = world.get_snapshot()
        wall = time.perf_counter()
        time.sleep(2.0)
        final = world.get_snapshot()
        hz = (final.frame - initial.frame) / (time.perf_counter() - wall)
        vehicles = list(world.get_actors().filter("vehicle.*"))
        print("preflight", "editor_minimized", states, "world_hz", hz, "vehicles", [v.id for v in vehicles])
        print("world_settings", world.get_settings())
        if states != [False] or hz < 20.0 or vehicles:
            print("BLOCKED: operational readiness check; no actor spawned")
            return

        confirmation_ready_at: float | None = None

        def input_source() -> DriverInput:
            nonlocal confirmation_ready_at
            subject = session.exit_assistance
            if subject is None:
                raise RuntimeError("P5 coordinator missing")
            if subject.status is ExitAssistanceStatus.COMPLETED:
                return DriverInput(brake=0.5)
            if subject.status is ExitAssistanceStatus.PRESENTED:
                if confirmation_ready_at is None:
                    confirmation_ready_at = time.monotonic() + 2.5
                return DriverInput(exit_confirm_requested=time.monotonic() >= confirmation_ready_at)
            return DriverInput()

        def record_control(step: CarlaNoAControlStep) -> None:
            geometry = step.lane_geometry
            trace.append((
                step.frame, step.simulation_time_seconds, time.monotonic_ns(),
                geometry.vehicle_pose.x, geometry.vehicle_pose.y, geometry.vehicle_pose.yaw_rad,
                geometry.road_id, geometry.section_id, geometry.lane_id,
                geometry.waypoint_pose.x, geometry.waypoint_pose.y,
                geometry.geometry.lateral_error_m, geometry.geometry.heading_error_rad,
                step.measured_speed_kmh, step.command.throttle, step.command.brake, step.command.steering,
                session.exit_assistance.status.value,
            ))

        runner = ResearchNoARunner(parse_arguments(arguments), client=client,
                                   driver_input_source=input_source, control_trace=record_control)
        try:
            with runner.session() as session:
                report = session.run()
                if report is not None:
                    print(report.format())
                print("P5_status", session.exit_assistance.status.value)
        finally:
            with (directory / "control_trace.csv").open("x", newline="", encoding="utf-8") as destination:
                writer = csv.writer(destination)
                writer.writerow(("frame", "simulation_seconds", "host_monotonic_ns", "x", "y", "yaw_rad",
                                 "reference_road", "reference_section", "reference_lane", "reference_x", "reference_y",
                                 "lateral_error_m", "heading_error_rad", "speed_kmh", "throttle", "brake", "steering", "p5_status"))
                writer.writerows(trace)
            print("source_unchanged", hashes == source_hashes(root))
    print("completed", directory, flush=True)


if __name__ == "__main__":
    main()
