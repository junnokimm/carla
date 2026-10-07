from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from src.experiment.assignment import load_assignment_csv
from src.experiment.automation_interaction import AutomationInteractionConfig
from src.experiment.context import (
    ExperimentModule,
    Module1Condition,
    Module2Condition,
    ParticipantId,
    StudyRunId,
)
from src.experiment.exit_assistance import ExitContext
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_config import (
    DEFAULT_RESEARCH_VEHICLE_BLUEPRINT,
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated research NoA validation surface."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        dest="mode",
        action="store_const",
        const=ResearchNoARunMode.DRY_RUN,
        help="Keep manual input disabled and never activate custom control.",
    )
    mode.add_argument(
        "--live-smoke",
        dest="mode",
        action="store_const",
        const=ResearchNoARunMode.LIVE_SMOKE,
        help="Run the bounded custom-control smoke after spawn preflight.",
    )
    parser.add_argument("--spawn-index", type=int)
    parser.add_argument("--duration", required=False, type=float, default=5.0)
    parser.add_argument(
        "--development-validation",
        action="store_true",
        help=(
            "P5 local development validation only: allow live-smoke duration up to "
            "120 seconds with exit assistance configured. Other safety caps remain "
            "unchanged. Not for production experiments or the final protocol."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--vehicle-blueprint", default=DEFAULT_RESEARCH_VEHICLE_BLUEPRINT
    )
    parser.add_argument("--front-camera-only", action="store_true")
    parser.add_argument("--pi-trace", action="store_true")
    parser.add_argument("--camera-diagnostics", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--assignment-file")
    parser.add_argument("--participant-id")
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--automation-module", choices=[item.value for item in ExperimentModule]
    )
    parser.add_argument(
        "--automation-condition",
        choices=[item.value for item in (*Module1Condition, *Module2Condition)],
    )
    parser.add_argument("--activation-center-tolerance-m", type=float)
    parser.add_argument("--activation-heading-tolerance-rad", type=float)
    parser.add_argument("--driver-brake-threshold", type=float)
    parser.add_argument("--button-deactivation", choices=("enabled", "disabled"))
    parser.add_argument("--initial-lane-change-in-progress", action="store_true")
    parser.add_argument("--interaction-stage")
    parser.add_argument("--noa-key", default="n")
    parser.add_argument(
        "--exit-route-manifest",
        default="config/town04_exit_routes_dev_v3.json",
    )
    parser.add_argument("--exit-route-id")
    parser.add_argument("--exit-event-id")
    parser.add_argument(
        "--exit-context", choices=[item.value for item in ExitContext], default="MODULE"
    )
    parser.add_argument("--exit-navigation-trigger-m", type=float, default=1000.0)
    parser.add_argument("--exit-response-timeout-s", type=float, default=5.0)
    parser.add_argument("--exit-lateral-onset-m", type=float, default=0.15)
    parser.add_argument("--exit-confirm-key", default="c")
    parser.add_argument("--exit-reject-key", default="r")
    parser.add_argument("--target-speed-kmh", required=True, type=float)
    parser.add_argument("--speed-deadband-kmh", required=True, type=float)
    parser.add_argument("--acceleration-gain", required=True, type=float)
    parser.add_argument("--braking-gain", required=True, type=float)
    parser.add_argument("--integral-gain", required=True, type=float)
    parser.add_argument("--max-throttle", required=True, type=float)
    parser.add_argument("--max-brake", required=True, type=float)
    parser.add_argument("--lateral-error-gain", required=True, type=float)
    parser.add_argument("--heading-error-gain", required=True, type=float)
    parser.add_argument("--lateral-deadband-m", required=True, type=float)
    parser.add_argument("--heading-deadband-rad", required=True, type=float)
    parser.add_argument("--max-steering", required=True, type=float)
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> ResearchNoARunConfig:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode is ResearchNoARunMode.LIVE_SMOKE and args.spawn_index is None:
        parser.error("--spawn-index is required with --live-smoke")
    try:
        persistence = _parse_persistence(parser, args)
        control_config = NoAControlConfig(
            longitudinal=LongitudinalControlConfig(
                target_speed_kmh=args.target_speed_kmh,
                speed_deadband_kmh=args.speed_deadband_kmh,
                acceleration_gain=args.acceleration_gain,
                braking_gain=args.braking_gain,
                max_throttle=args.max_throttle,
                max_brake=args.max_brake,
                integral_gain=args.integral_gain,
            ),
            lateral=LateralControlConfig(
                lateral_error_gain=args.lateral_error_gain,
                heading_error_gain=args.heading_error_gain,
                lateral_deadband_m=args.lateral_deadband_m,
                heading_deadband_rad=args.heading_deadband_rad,
                max_steering=args.max_steering,
            ),
        )
        interaction = _parse_interaction(parser, args, persistence)
        exit_assistance = _parse_exit_assistance(parser, args)
        return ResearchNoARunConfig(
            duration=args.duration,
            control_config=control_config,
            mode=args.mode,
            spawn_index=args.spawn_index,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            vehicle_blueprint=args.vehicle_blueprint,
            front_camera_only=args.front_camera_only,
            pi_trace=args.pi_trace,
            camera_diagnostics=args.camera_diagnostics,
            run_id=args.run_id,
            automation_interaction=interaction,
            persistence=persistence,
            exit_assistance=exit_assistance,
            development_validation=args.development_validation,
        )
    except ValueError as error:
        parser.error(str(error))


def _parse_persistence(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> ResearchPersistenceConfig | None:
    supplied = (args.assignment_file, args.participant_id, args.output_dir)
    if not any(value is not None for value in supplied):
        return None
    if not all(value is not None for value in supplied):
        parser.error(
            "--assignment-file, --participant-id, and --output-dir are required together"
        )
    required = {
        "--run-id": args.run_id,
        "--automation-module": args.automation_module,
        "--interaction-stage": args.interaction_stage,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error(f"{', '.join(missing)} required with --assignment-file")
    assignment = load_assignment_csv(args.assignment_file).lookup(
        ParticipantId(args.participant_id)
    )
    return ResearchPersistenceConfig(
        assignment,
        StudyRunId(args.run_id),
        Path(args.output_dir),
        ExperimentModule(args.automation_module),
        args.interaction_stage,
    )


def _parse_interaction(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    persistence: ResearchPersistenceConfig | None,
) -> ResearchAutomationInteractionConfig | None:
    if args.automation_module is None:
        return None
    required = {
        "--activation-center-tolerance-m": args.activation_center_tolerance_m,
        "--activation-heading-tolerance-rad": args.activation_heading_tolerance_rad,
        "--driver-brake-threshold": args.driver_brake_threshold,
        "--button-deactivation": args.button_deactivation,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error(f"{', '.join(missing)} required with --automation-module")
    module = ExperimentModule(args.automation_module)
    if persistence is not None:
        condition = persistence.condition
        if (
            args.automation_condition is not None
            and args.automation_condition != condition.value
        ):
            parser.error("--automation-condition conflicts with participant assignment")
    else:
        if args.automation_condition is None:
            parser.error("--automation-condition required with --automation-module")
        condition = (
            Module1Condition(args.automation_condition)
            if module is ExperimentModule.MODULE_1
            else Module2Condition(args.automation_condition)
        )
    return ResearchAutomationInteractionConfig(
        module=module,
        condition=condition,
        policy=AutomationInteractionConfig(
            center_tolerance_m=args.activation_center_tolerance_m,
            heading_tolerance_rad=args.activation_heading_tolerance_rad,
            driver_brake_threshold=args.driver_brake_threshold,
            button_deactivation_enabled=args.button_deactivation == "enabled",
        ),
        initial_lane_change_in_progress=args.initial_lane_change_in_progress,
        initial_stage=args.interaction_stage,
        noa_key=args.noa_key.lower(),
    )


def _parse_exit_assistance(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> ResearchExitAssistanceConfig | None:
    if args.exit_route_id is None and args.exit_event_id is None:
        return None
    if args.exit_route_id is None or args.exit_event_id is None:
        parser.error("--exit-route-id and --exit-event-id are required together")
    return ResearchExitAssistanceConfig(
        manifest_path=Path(args.exit_route_manifest),
        route_id=args.exit_route_id,
        lc_event_id=args.exit_event_id,
        context=ExitContext(args.exit_context),
        navigation_trigger_distance_m=args.exit_navigation_trigger_m,
        response_timeout_s=args.exit_response_timeout_s,
        lateral_onset_threshold_m=args.exit_lateral_onset_m,
        confirm_key=args.exit_confirm_key.lower(),
        reject_key=args.exit_reject_key.lower(),
    )
