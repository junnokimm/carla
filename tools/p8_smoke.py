# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# Run with the existing CARLA .venv: python -m tools.p8_smoke --help
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from uuid import uuid4

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.assignment import assigned_participant, development_assignment
from src.p8.model import Mode, P8Error, Stage
from src.p8.orchestrator import SessionManager
from src.p8.preflight import inspect
from src.p8.readiness import load_readiness
from src.p8.runner import run_development
from src.p8.services import SessionServices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P8 guarded development runner; participant runtime remains blocked"
    )
    parser.add_argument(
        "--mode",
        choices=("dry", "accelerated", "live-preflight", "dev-live-drive"),
        default="dry",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--matrix", action="store_true")
    group.add_argument("--reset-smoke", action="store_true")
    parser.add_argument("--m1", choices=[item.value for item in Module1Condition])
    parser.add_argument("--m2", choices=[item.value for item in Module2Condition])
    parser.add_argument("--assignment-file", type=Path)
    parser.add_argument("--participant-id")
    parser.add_argument(
        "--abort-at",
        choices=[
            item.value
            for item in Stage
            if item not in (Stage.SESSION_COMPLETE, Stage.ABORTED)
        ],
    )
    parser.add_argument(
        "--readiness-file", type=Path, default=Path("config/p8_readiness.json")
    )
    parser.add_argument("--output-root", type=Path, default=Path("data/p8_full_flow"))
    parser.add_argument(
        "--tobii-python", type=Path, default=Path(".venv-tobii/Scripts/python.exe")
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Only for live-preflight: no CARLA or worker process probes",
    )
    parser.add_argument("--ack-dev-live", action="store_true")
    parser.add_argument("--spawn-index", type=int)
    parser.add_argument(
        "--duration",
        type=float,
        help="Bounded DEV drive seconds: max 60 normally, max 120 for P5 validation.",
    )
    parser.add_argument("--front-camera-only", action="store_true")
    parser.add_argument("--exit-route-manifest", type=Path)
    parser.add_argument("--exit-route-id")
    parser.add_argument("--development-validation", action="store_true")
    parser.add_argument("--noa-desired-speed-kmh", type=float)
    parser.add_argument("--dev-speed-limit-kmh", type=float)
    parser.add_argument("--ack-high-speed-dev", action="store_true")
    parser.add_argument("--noa-max-throttle", type=float)
    parser.add_argument("--pi-trace", action="store_true")
    args = parser.parse_args(argv)
    if args.assignment_file is not None and (
        args.m1 or args.m2 or args.matrix or args.reset_smoke
    ):
        parser.error("preassigned CSV conditions cannot be overridden")
    if (args.assignment_file is None) != (args.participant_id is None):
        parser.error("--assignment-file and --participant-id must be supplied together")
    if (args.matrix or args.reset_smoke) and (args.m1 or args.m2):
        parser.error(
            "matrix/reset smoke has explicit DEV combinations; do not supply --m1/--m2"
        )
    if args.offline and args.mode != "live-preflight":
        parser.error("--offline only applies to live-preflight")
    if args.mode == "live-preflight" and any(
        (
            args.matrix,
            args.reset_smoke,
            args.abort_at,
            args.assignment_file,
            args.m1,
            args.m2,
        )
    ):
        parser.error("live-preflight does not execute a session or matrix")
    if args.mode == "dev-live-drive":
        host = "127.0.0.1" if args.host is None else args.host
        port = 2000 if args.port is None else args.port
        if not args.ack_dev_live:
            parser.error("--ack-dev-live is required for DEV live driving")
        if args.spawn_index is None:
            parser.error("--spawn-index is required for DEV live driving")
        if args.spawn_index < 0:
            parser.error("--spawn-index must be >= 0")
        duration = 10.0 if args.duration is None else args.duration
        p5_requested = (
            args.exit_route_manifest is not None or args.exit_route_id is not None
        )
        if args.ack_high_speed_dev and (
            args.noa_desired_speed_kmh is None or args.dev_speed_limit_kmh is None
        ):
            parser.error(
                "--ack-high-speed-dev requires explicit --noa-desired-speed-kmh "
                "and --dev-speed-limit-kmh"
            )
        if args.dev_speed_limit_kmh is not None and args.noa_desired_speed_kmh is None:
            parser.error("--dev-speed-limit-kmh requires --noa-desired-speed-kmh")
        if (
            args.noa_desired_speed_kmh is not None
            and args.noa_desired_speed_kmh > 20.0
            and args.dev_speed_limit_kmh is None
        ):
            parser.error(
                "desired speed above 20 requires --dev-speed-limit-kmh and "
                "--ack-high-speed-dev"
            )
        high_speed_requested = (args.noa_desired_speed_kmh or 0.0) > 20.0 or (
            args.dev_speed_limit_kmh or 0.0
        ) > 20.0
        if high_speed_requested and not args.ack_high_speed_dev:
            parser.error("high-speed DEV requires --ack-high-speed-dev")
        if high_speed_requested and p5_requested:
            parser.error("high-speed DEV cannot be combined with P5 exit assistance")
        noa_max_throttle = (
            0.4 if args.noa_max_throttle is None else args.noa_max_throttle
        )
        if not isfinite(noa_max_throttle) or not 0.0 < noa_max_throttle <= 1.0:
            parser.error("--noa-max-throttle must be finite and in (0, 1]")
        if noa_max_throttle > 0.4 and not (
            args.ack_high_speed_dev
            and args.noa_desired_speed_kmh is not None
            and args.dev_speed_limit_kmh is not None
        ):
            parser.error(
                "--noa-max-throttle above 0.4 requires the complete acknowledged "
                "high-speed DEV profile"
            )
        try:
            from src.scenario.research_noa_speed_policy import (
                RESEARCH_MAX_ALLOWED_SPEED_KMH,
                ResearchSpeedPolicy,
            )

            speed_policy = (
                None
                if args.dev_speed_limit_kmh is None
                else ResearchSpeedPolicy(
                    args.dev_speed_limit_kmh,
                    args.ack_high_speed_dev,
                )
            )
            if (
                speed_policy is not None
                and args.noa_desired_speed_kmh is not None
                and args.noa_desired_speed_kmh
                >= speed_policy.operational_guard_speed_kmh
            ):
                parser.error(
                    "--noa-desired-speed-kmh must be below --dev-speed-limit-kmh"
                )
            if args.noa_desired_speed_kmh is not None and (
                not isfinite(args.noa_desired_speed_kmh)
                or not 0.0
                < args.noa_desired_speed_kmh
                <= RESEARCH_MAX_ALLOWED_SPEED_KMH
            ):
                parser.error("--noa-desired-speed-kmh must be finite and in (0, 110]")
        except ValueError as error:
            parser.error(str(error))
        if (args.exit_route_manifest is None) != (args.exit_route_id is None):
            parser.error(
                "--exit-route-manifest and --exit-route-id are required together"
            )
        if args.development_validation and not p5_requested:
            parser.error(
                "--development-validation requires explicit P5 exit route options"
            )
        duration_cap = 120.0 if args.development_validation else 60.0
        if not isfinite(duration) or not 0.0 < duration <= duration_cap:
            parser.error(f"--duration must be finite and in (0, {duration_cap:g}]")
        if not host.strip():
            parser.error("--host must not be empty")
        if not 1 <= port <= 65535:
            parser.error("--port must be in 1..65535")
        if any((args.matrix, args.reset_smoke, args.abort_at)):
            parser.error(
                "DEV live driving is one partial segment, not a matrix/full-flow run"
            )
        if p5_requested:
            from src.vehicle.carla_exit_route import load_exit_route_manifest

            try:
                load_exit_route_manifest(args.exit_route_manifest).route(
                    args.exit_route_id
                )
            except (OSError, ValueError, KeyError) as error:
                parser.error(f"invalid P5 exit route: {error}")
    elif (
        args.ack_dev_live
        or args.spawn_index is not None
        or args.duration is not None
        or args.front_camera_only
        or args.exit_route_manifest is not None
        or args.exit_route_id is not None
        or args.development_validation
        or args.noa_desired_speed_kmh is not None
        or args.dev_speed_limit_kmh is not None
        or args.ack_high_speed_dev
        or args.noa_max_throttle is not None
        or args.pi_trace
    ):
        parser.error("DEV live options require --mode dev-live-drive")
    if args.mode == "live-preflight":
        host = "127.0.0.1" if args.host is None else args.host
        port = 2000 if args.port is None else args.port
        if not host.strip():
            parser.error("--host must not be empty")
        if not 1 <= port <= 65535:
            parser.error("--port must be in 1..65535")
    elif args.mode != "dev-live-drive" and (
        args.host is not None or args.port is not None
    ):
        parser.error("--host/--port require a live mode")
    batch = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    directory = args.output_root / batch
    manager = None
    live_services = None
    try:
        if args.mode == "live-preflight":
            report = inspect(
                directory,
                readiness_file=args.readiness_file,
                tobii_python=args.tobii_python,
                host=host,
                port=port,
                offline=args.offline,
            )
            print(json.dumps({**report, "output": str(directory)}))
            return 2
        readiness, history = load_readiness(args.readiness_file)
        if args.mode == "dev-live-drive":
            from src.p8.live_drive import (
                LiveDriveOptions,
                build_live_drive_config,
                run_live_drive,
            )
            from src.scenario.research_noa_runtime import ResearchNoARunner

            assignment = (
                assigned_participant(args.assignment_file, args.participant_id)
                if args.assignment_file is not None
                else development_assignment(
                    Module1Condition(args.m1 or "NO_SURT"),
                    Module2Condition(args.m2 or "MANUAL"),
                )
            )
            if args.exit_route_id is not None:
                if (
                    args.assignment_file is not None
                    and assignment.route_id != args.exit_route_id
                ):
                    parser.error("assigned route_id must match --exit-route-id")
                assignment = replace(assignment, route_id=args.exit_route_id)
            live_services = SessionServices(
                args.output_root,
                assignment,
                batch,
                Mode.DEV_LIVE_DRIVE,
            )
            config = build_live_drive_config(
                live_services,
                LiveDriveOptions(
                    duration=duration,
                    spawn_index=args.spawn_index,
                    host=host,
                    port=port,
                    front_camera_only=args.front_camera_only,
                    exit_manifest=args.exit_route_manifest,
                    exit_route_id=args.exit_route_id,
                    development_validation=args.development_validation,
                    noa_desired_speed_kmh=args.noa_desired_speed_kmh,
                    speed_policy=speed_policy,
                    noa_max_throttle=noa_max_throttle,
                    pi_trace=args.pi_trace,
                ),
            )
            runner = ResearchNoARunner(
                config,
                borrowed_log=live_services.borrowed_research_log(),
            )
            live_services.provenance = (
                "REAL_CARLA_RUNTIME_DEV_PARTIAL"
                if runner.client_source == "REAL_CARLA_RPC"
                and runner.driver_input_source == "REAL_KEYBOARD_PATH"
                else "DEV_STUB"
            )
            result = run_live_drive(
                live_services,
                runner,
                reported_history=history,
                assignment_source=(
                    "P4_PREASSIGNED_CSV"
                    if args.assignment_file
                    else "DEV_FORCED_NOT_RANDOMIZATION"
                ),
            )
            print(
                json.dumps(
                    {
                        "output": str(live_services.directory),
                        "status": result["completion_status"],
                        "participant_ready": False,
                    }
                )
            )
            return (
                0 if result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE" else 2
            )
        manager = SessionManager(
            args.output_root, mode=Mode(args.mode), readiness=readiness
        )
        if args.assignment_file is not None:
            assignments = [
                assigned_participant(args.assignment_file, args.participant_id)
            ]
        elif args.matrix:
            assignments = [
                development_assignment(m1, m2)
                for m1 in Module1Condition
                for m2 in Module2Condition
            ]
        elif args.reset_smoke:
            assignments = [
                development_assignment(Module1Condition.SURT, Module2Condition.NOA_L2),
                development_assignment(),
            ]
        else:
            assignments = [
                development_assignment(
                    Module1Condition(args.m1 or "NO_SURT"),
                    Module2Condition(args.m2 or "MANUAL"),
                )
            ]
        directory.mkdir(parents=True, exist_ok=False)
        reports = []
        for index, assignment in enumerate(assignments):
            run_id = f"{batch}-{index + 1}"
            session = manager.create(assignment, run_id)
            session.services.emit(
                "p8_assignment_provenance",
                {
                    "source": "P4_PREASSIGNED_CSV"
                    if args.assignment_file
                    else "DEV_FORCED_NOT_RANDOMIZATION",
                    "reported_history": history,
                },
            )
            result = run_development(
                session,
                abort_at=None if args.abort_at is None else Stage(args.abort_at),
            )
            reports.append(
                {
                    "run_id": run_id,
                    "participant_id": assignment.participant_id,
                    "conditions": result["assigned_conditions"],
                    "status": result["completion_status"],
                    "manifest": str(
                        session.services.directory / "session_manifest.json"
                    ),
                    "initial_state": result["initial_components"],
                    "cleanup": result["cleanup"],
                    "software_artifacts_complete": result["completeness"][
                        "software_artifacts_complete"
                    ],
                }
            )
        summary = {
            "mode": args.mode,
            "matrix": args.matrix,
            "reset_smoke": args.reset_smoke,
            "participant_run": False,
            "development_mode": True,
            "participant_ready": False,
            "runs": reports,
            "all_dev_flows_complete": all(
                row["status"] == "DEV_FLOW_COMPLETE" for row in reports
            ),
        }
        with (directory / "batch_summary.json").open("x", encoding="utf-8") as output:
            json.dump(summary, output, indent=2)
        print(
            json.dumps(
                {
                    "output": str(directory),
                    "run_count": len(reports),
                    "statuses": [row["status"] for row in reports],
                    "participant_ready": False,
                }
            )
        )
        return 0 if summary["all_dev_flows_complete"] else 2
    except (P8Error, OSError, ValueError, RuntimeError) as error:
        if (
            manager is not None
            and manager.active is not None
            and not manager.active.closed
        ):
            manager.active.abort(str(error))
        if live_services is not None and not live_services.closed:
            live_services.close()
        print(
            json.dumps(
                {
                    "status": "FAILED_CLOSED",
                    "reason": str(error),
                    "participant_ready": False,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
