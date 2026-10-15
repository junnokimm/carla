from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from src.experiment.automation_interaction import AutomationInteractionConfig
from src.experiment.context import Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitContext
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.p8.model import Mode, P8Error
from src.p8.native_tm_diagnostics import NativeTrafficManagerDiagnosticsConfig
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import OperatorRuntimeConfig, RegistrationRequest
from src.p8.operator_ui_runtime import run_operator_ui
from src.p8.operator_ui_types import OperatorUiConfig
from src.p8.readiness import load_readiness
from src.p8.tm_runtime_attestation import (
    TrafficManagerBinaryProfile,
    TrafficManagerRuntimeAttestor,
)
from src.p8.tsxw_policy import TsxwConfig
from src.scenario.research_exit_config import (
    ExitEligibilityWindow,
    ResearchExitAssistanceConfig,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.vehicle.carla_exit_route import load_exit_route_manifest


@dataclass(frozen=True, slots=True)
class ExitBehaviorArguments:
    navigation_trigger_distance_m: float
    response_timeout_s: float
    lateral_onset_threshold_m: float


@dataclass(frozen=True, slots=True)
class ExitRouteSelection:
    manifest_path: Path
    route_id: str
    context: ExitContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified P8 operator UI; participant execution remains blocked"
    )
    parser.add_argument(
        "--mode",
        choices=(Mode.DEV_REHEARSAL.value, Mode.PARTICIPANT.value),
        default=Mode.DEV_REHEARSAL.value,
    )
    parser.add_argument("--participant-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--assignment-file", type=Path)
    parser.add_argument("--m1", choices=[item.value for item in Module1Condition])
    parser.add_argument("--m2", choices=[item.value for item in Module2Condition])
    parser.add_argument("--ack-dev-live", action="store_true")
    parser.add_argument("--spawn-index", type=int)
    parser.add_argument("--traffic-manager-port", type=int, default=8000)
    parser.add_argument("--native-tm-diagnostics", action="store_true")
    parser.add_argument("--native-tm-diagnostics-capacity", type=int)
    parser.add_argument("--native-tm-diagnostics-interval", type=float)
    parser.add_argument("--drive-duration", type=float)
    parser.add_argument("--training-duration", type=float, default=600.0)
    parser.add_argument("--rear-mirror", action="store_true")
    parser.add_argument("--tsxw", action="store_true")
    parser.add_argument("--tsxw-index", type=int)
    parser.add_argument("--tsxw-name")
    parser.add_argument("--tsxw-steering-deadzone", type=float)
    parser.add_argument("--tsxw-steering-left-axis", type=float)
    parser.add_argument("--tsxw-steering-center-axis", type=float)
    parser.add_argument("--tsxw-steering-right-axis", type=float)
    parser.add_argument("--tsxw-brake-pressed-axis", type=float)
    parser.add_argument("--exit-route-manifest", type=Path)
    parser.add_argument("--exit-route-id")
    parser.add_argument("--training-exit-route-manifest", type=Path)
    parser.add_argument("--training-exit-route-id")
    parser.add_argument("--exit-navigation-trigger-distance-m", type=float)
    parser.add_argument("--exit-response-timeout-s", type=float)
    parser.add_argument("--exit-lateral-onset-threshold-m", type=float)
    parser.add_argument("--m1-exit-open-s", type=float)
    parser.add_argument("--m1-exit-close-s", type=float)
    parser.add_argument("--m2-exit-open-s", type=float)
    parser.add_argument("--m2-exit-close-s", type=float)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output-root", type=Path, default=Path("data/p8_operator"))
    parser.add_argument(
        "--readiness-file", type=Path, default=Path("config/p8_readiness.json")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = Mode(args.mode)
    diagnostic_tuning = (
        args.native_tm_diagnostics_capacity is not None
        or args.native_tm_diagnostics_interval is not None
    )
    if diagnostic_tuning and not args.native_tm_diagnostics:
        parser.error("native TM diagnostic tuning requires --native-tm-diagnostics")
    if args.native_tm_diagnostics and mode is not Mode.DEV_REHEARSAL:
        parser.error("--native-tm-diagnostics is DEV_REHEARSAL-only")
    if mode is Mode.DEV_REHEARSAL:
        if not args.ack_dev_live:
            parser.error("--ack-dev-live is required before bounded Runner use")
        if args.spawn_index is None or args.spawn_index < 0:
            parser.error("--spawn-index >= 0 is required before bounded Runner use")
    if not 1.0 <= args.training_duration <= 600.0:
        parser.error("--training-duration must be in 1..600 seconds")
    if args.drive_duration is not None and not 1.0 <= args.drive_duration <= 60.0:
        parser.error("--drive-duration must be in 1..60 seconds")
    tsxw_options = (
        args.tsxw_index,
        args.tsxw_name,
        args.tsxw_steering_deadzone,
        args.tsxw_steering_left_axis,
        args.tsxw_steering_center_axis,
        args.tsxw_steering_right_axis,
        args.tsxw_brake_pressed_axis,
    )
    if args.tsxw and (args.tsxw_index is None and args.tsxw_name is None):
        parser.error("--tsxw requires --tsxw-index and/or --tsxw-name")
    if args.tsxw and args.tsxw_steering_deadzone is None:
        parser.error("--tsxw requires explicit --tsxw-steering-deadzone")
    if not args.tsxw and any(value is not None for value in tsxw_options):
        parser.error("TS-XW options require --tsxw")
    if (
        not args.host.strip()
        or not 1 <= args.port <= 65535
        or not 1 <= args.traffic_manager_port <= 65535
        or not isfinite(args.timeout)
        or args.timeout <= 0
    ):
        parser.error("host, port, and timeout must describe a valid bounded endpoint")
    explicit = args.m1 is not None or args.m2 is not None
    if args.assignment_file is not None and explicit:
        parser.error("preassigned CSV conditions cannot be overridden")
    if args.assignment_file is None and mode is Mode.PARTICIPANT:
        parser.error("participant mode requires --assignment-file")
    if args.assignment_file is None and (args.m1 is None or args.m2 is None):
        parser.error("DEV registration requires explicit --m1 and --m2")
    if (args.exit_route_manifest is None) != (args.exit_route_id is None):
        parser.error("--exit-route-manifest and --exit-route-id are required together")
    if (args.training_exit_route_manifest is None) != (
        args.training_exit_route_id is None
    ):
        parser.error(
            "--training-exit-route-manifest and --training-exit-route-id "
            "are required together"
        )
    module_exit_enabled = args.exit_route_manifest is not None
    training_exit_enabled = args.training_exit_route_manifest is not None
    exit_behavior_values = (
        args.exit_navigation_trigger_distance_m,
        args.exit_response_timeout_s,
        args.exit_lateral_onset_threshold_m,
    )
    if (module_exit_enabled or training_exit_enabled) and any(
        value is None for value in exit_behavior_values
    ):
        parser.error("explicit exit behavior values are required with P5 selection")
    if not (module_exit_enabled or training_exit_enabled) and any(
        value is not None for value in exit_behavior_values
    ):
        parser.error("exit behavior values require a selected P5 route")
    if module_exit_enabled and args.drive_duration is not None:
        parser.error("--drive-duration cannot be used with a module P5 route")
    timing_values = (
        args.m1_exit_open_s,
        args.m1_exit_close_s,
        args.m2_exit_open_s,
        args.m2_exit_close_s,
    )
    if module_exit_enabled and any(value is None for value in timing_values):
        parser.error("module P5 requires explicit M1/M2 exit eligibility windows")
    if not module_exit_enabled and any(value is not None for value in timing_values):
        parser.error("module exit eligibility windows require a module P5 route")
    readiness, _ = load_readiness(args.readiness_file)
    try:
        runtime = _runtime_config(args)
    except ValueError as error:
        parser.error(str(error))
    request = RegistrationRequest(
        participant_id=args.participant_id,
        run_id=args.run_id,
        assignment_file=args.assignment_file,
        m1_condition=None if args.m1 is None else Module1Condition(args.m1),
        m2_condition=None if args.m2 is None else Module2Condition(args.m2),
        route_id=None if runtime.module_exit is None else runtime.module_exit.route_id,
    )
    controller = OperatorController(
        args.output_root,
        mode=mode,
        runtime=runtime,
        readiness=readiness,
    )
    try:
        return run_operator_ui(
            controller,
            OperatorUiConfig(request, args.training_duration, args.drive_duration),
        )
    except KeyboardInterrupt:
        _abort_open_session(controller, "OPERATOR_CLI_INTERRUPTED")
        print(json.dumps({"status": "FAILED_CLOSED", "reason": "KeyboardInterrupt"}))
        return 130
    except (P8Error, OSError, RuntimeError, ValueError) as error:
        _abort_open_session(
            controller,
            f"OPERATOR_CLI_FAILURE:{type(error).__name__}:{error}",
        )
        print(json.dumps({"status": "FAILED_CLOSED", "reason": str(error)}))
        return 2


def _abort_open_session(controller: OperatorController, reason: str) -> None:
    try:
        session = controller.session
    except P8Error:
        return
    if not session.closed:
        controller.abort(reason)


def _runtime_config(args: argparse.Namespace) -> OperatorRuntimeConfig:
    control = NoAControlConfig(
        longitudinal=LongitudinalControlConfig(
            None,
            0.2,
            0.1,
            0.1,
            1.0,
            0.5,
            0.02,
        ),
        lateral=LateralControlConfig(0.2, 0.5, 0.1, 0.05, 0.15),
    )

    profile = (
        TrafficManagerBinaryProfile.NATIVE_DIAGNOSTIC_DEV
        if args.native_tm_diagnostics
        else TrafficManagerBinaryProfile.DEFAULT
    )

    def build(config, borrowed_log):
        return ResearchNoARunner(
            config,
            borrowed_log=borrowed_log,
            tm_runtime_attestor=TrafficManagerRuntimeAttestor.for_profile(profile),
        )

    training_selection = (
        None
        if args.training_exit_route_manifest is None
        else ExitRouteSelection(
            args.training_exit_route_manifest,
            args.training_exit_route_id,
            ExitContext.TRAINING,
        )
    )
    module_selection = (
        None
        if args.exit_route_manifest is None
        else ExitRouteSelection(
            args.exit_route_manifest,
            args.exit_route_id,
            ExitContext.MODULE,
        )
    )
    if module_selection is None and training_selection is None:
        module_exit = training_exit = None
    else:
        behavior = ExitBehaviorArguments(
            args.exit_navigation_trigger_distance_m,
            args.exit_response_timeout_s,
            args.exit_lateral_onset_threshold_m,
        )
        assisted_execution = args.traffic_manager_port is not None
        module_exit = _exit_template(module_selection, behavior, assisted_execution)
        training_exit = _exit_template(training_selection, behavior, assisted_execution)

    return OperatorRuntimeConfig(
        spawn_index=args.spawn_index or 0,
        control_config=control,
        speed_policy=None,
        interaction_policy=AutomationInteractionConfig(
            0.2,
            0.1,
            0.05,
            True,
            True,
        ),
        runner_builder=build,
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        rear_mirror_enabled=args.rear_mirror,
        module_exit=module_exit,
        training_exit=training_exit,
        traffic_manager_port=args.traffic_manager_port,
        module_1_exit_window=(
            None
            if module_selection is None
            else ExitEligibilityWindow(
                args.m1_exit_open_s,
                args.m1_exit_close_s,
            )
        ),
        module_2_exit_window=(
            None
            if module_selection is None
            else ExitEligibilityWindow(
                args.m2_exit_open_s,
                args.m2_exit_close_s,
            )
        ),
        native_tm_diagnostics=(
            None
            if not args.native_tm_diagnostics
            else NativeTrafficManagerDiagnosticsConfig(
                capacity=(
                    1024
                    if args.native_tm_diagnostics_capacity is None
                    else args.native_tm_diagnostics_capacity
                ),
                interval_seconds=(
                    0.5
                    if args.native_tm_diagnostics_interval is None
                    else args.native_tm_diagnostics_interval
                ),
            )
        ),
        tsxw_config=(
            None
            if not args.tsxw
            else TsxwConfig(
                index=args.tsxw_index,
                name=args.tsxw_name,
                steering_deadzone=args.tsxw_steering_deadzone,
                steering_left_axis=(
                    -1.0
                    if args.tsxw_steering_left_axis is None
                    else args.tsxw_steering_left_axis
                ),
                steering_center_axis=(
                    0.0
                    if args.tsxw_steering_center_axis is None
                    else args.tsxw_steering_center_axis
                ),
                steering_right_axis=(
                    1.0
                    if args.tsxw_steering_right_axis is None
                    else args.tsxw_steering_right_axis
                ),
                brake_pressed_axis=(
                    -1.0
                    if args.tsxw_brake_pressed_axis is None
                    else args.tsxw_brake_pressed_axis
                ),
            )
        ),
    )


def _exit_template(
    selection: ExitRouteSelection | None,
    behavior: ExitBehaviorArguments,
    assisted_execution: bool,
) -> ResearchExitAssistanceConfig | None:
    if selection is None:
        return None
    load_exit_route_manifest(selection.manifest_path).route(selection.route_id)
    return ResearchExitAssistanceConfig(
        manifest_path=selection.manifest_path,
        route_id=selection.route_id,
        lc_event_id=(f"UNBOUND:{selection.context.value}:{selection.route_id}"),
        context=selection.context,
        navigation_trigger_distance_m=behavior.navigation_trigger_distance_m,
        response_timeout_s=behavior.response_timeout_s,
        lateral_onset_threshold_m=behavior.lateral_onset_threshold_m,
        confirm_key="x",
        reject_key="r",
        assisted_execution_enabled=assisted_execution,
        indicator_confirmation=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
