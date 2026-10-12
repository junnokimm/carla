from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path

from src.experiment.automation_interaction import AutomationInteractionConfig
from src.experiment.context import ExperimentModule
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.p7.types import Json
from src.p8.live_drive_gate import validate_live_drive
from src.p8.manifest import inspect_outputs
from src.p8.model import P8Error, Stage
from src.p8.services import SessionServices
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_shutdown import ShutdownBrakeOutcome
from src.scenario.research_noa_speed_policy import (
    RESEARCH_MAX_ALLOWED_SPEED_KMH,
    ResearchSpeedPolicy,
)


@dataclass(frozen=True, slots=True)
class LiveDriveOptions:
    duration: float
    spawn_index: int
    host: str = "127.0.0.1"
    port: int = 2000
    timeout: float = 5.0
    front_camera_only: bool = False
    exit_manifest: Path | None = None
    exit_route_id: str | None = None
    development_validation: bool = False
    noa_desired_speed_kmh: float | None = None
    speed_policy: ResearchSpeedPolicy | None = None
    noa_max_throttle: float = 0.4
    pi_trace: bool = False

    def __post_init__(self) -> None:
        if (self.exit_manifest is None) != (self.exit_route_id is None):
            raise P8Error("P8_LIVE_P5_MANIFEST_ROUTE_REQUIRED_TOGETHER")
        if self.development_validation and self.exit_manifest is None:
            raise P8Error("P8_LIVE_P5_DEVELOPMENT_VALIDATION_REQUIRES_ROUTE")
        desired = self.noa_desired_speed_kmh
        if desired is not None and (
            not isfinite(desired) or not 0.0 < desired <= RESEARCH_MAX_ALLOWED_SPEED_KMH
        ):
            raise P8Error("P8_LIVE_DESIRED_SPEED_REQUIRES_FINITE_RANGE_0_TO_110")
        if self.speed_policy is not None and desired is None:
            raise P8Error("P8_LIVE_SPEED_LIMIT_REQUIRES_EXPLICIT_DESIRED_SPEED")
        if (
            self.speed_policy is not None
            and desired is not None
            and desired >= self.speed_policy.operational_guard_speed_kmh
        ):
            raise P8Error("P8_LIVE_DESIRED_SPEED_REQUIRES_GUARD_HEADROOM")
        if desired is not None and desired > 20.0 and self.speed_policy is None:
            raise P8Error("P8_LIVE_HIGH_SPEED_REQUIRES_EXPLICIT_POLICY")
        if (
            self.speed_policy is not None
            and self.speed_policy.high_speed_dev_acknowledged
            and (self.exit_manifest is not None or self.development_validation)
        ):
            raise P8Error("P8_LIVE_HIGH_SPEED_EXCLUDES_P5_AND_DEVELOPMENT_VALIDATION")
        if (
            not isfinite(self.noa_max_throttle)
            or not 0.0 < self.noa_max_throttle <= 1.0
        ):
            raise P8Error("P8_LIVE_NOA_MAX_THROTTLE_REQUIRES_FINITE_RANGE_0_TO_1")
        if self.noa_max_throttle > 0.4 and (
            self.speed_policy is None
            or not self.speed_policy.high_speed_dev_acknowledged
        ):
            raise P8Error("P8_LIVE_NOA_MAX_THROTTLE_REQUIRES_HIGH_SPEED_POLICY")


def build_live_drive_config(
    services: SessionServices,
    options: LiveDriveOptions,
) -> ResearchNoARunConfig:
    control = NoAControlConfig(
        longitudinal=LongitudinalControlConfig(
            target_speed_kmh=(
                10.0
                if options.noa_desired_speed_kmh is None
                else options.noa_desired_speed_kmh
            ),
            speed_deadband_kmh=0.2,
            acceleration_gain=0.1,
            braking_gain=0.1,
            max_throttle=options.noa_max_throttle,
            max_brake=0.5,
            integral_gain=0.02,
        ),
        lateral=LateralControlConfig(
            lateral_error_gain=0.2,
            heading_error_gain=0.5,
            lateral_deadband_m=0.1,
            heading_deadband_rad=0.05,
            max_steering=0.15,
        ),
    )
    interaction = ResearchAutomationInteractionConfig(
        ExperimentModule.MODULE_1,
        services.context.module_1_condition,
        AutomationInteractionConfig(0.2, 0.1, 0.05, True),
    )
    persistence = ResearchPersistenceConfig(
        services.assignment,
        services.context.study_run_id,
        services.directory,
        ExperimentModule.MODULE_1,
        "P8_DEV_LIVE_MODULE_1_PARTIAL",
    )
    exit_assistance = (
        None
        if options.exit_manifest is None or options.exit_route_id is None
        else ResearchExitAssistanceConfig(
            options.exit_manifest,
            options.exit_route_id,
            f"{services.context.study_run_id}:MODULE_1:{options.exit_route_id}",
        )
    )
    return ResearchNoARunConfig(
        duration=options.duration,
        control_config=control,
        mode=ResearchNoARunMode.LIVE_SMOKE,
        spawn_index=options.spawn_index,
        host=options.host,
        port=options.port,
        timeout=options.timeout,
        front_camera_only=options.front_camera_only,
        run_id=str(services.context.study_run_id),
        automation_interaction=interaction,
        persistence=persistence,
        exit_assistance=exit_assistance,
        development_validation=options.development_validation,
        speed_policy=options.speed_policy,
        pi_trace=options.pi_trace,
    )


def run_live_drive(
    services: SessionServices,
    runner: ResearchNoARunner,
    *,
    reported_history: dict[str, str],
    assignment_source: str = "DEV_FORCED_NOT_RANDOMIZATION",
) -> dict[str, Json]:
    validate_live_drive(services, runner)
    services.stage = Stage.MODULE_1_RUNNING
    sources: dict[str, Json] = {
        "carla": runner.client_source,
        "driver_input": runner.driver_input_source,
        "telemetry": "RUNTIME_CARLA_SNAPSHOT_ADAPTER",
        "p5_exit": (
            "ACTUAL_P5_CONFIGURED"
            if runner.config.exit_assistance is not None
            else "DISABLED"
        ),
        "p6_tablet": "DISABLED",
        "p6_gaze": "DISABLED",
        "p7_hazard": "DISABLED",
        "survey": "DISABLED",
    }
    shared = {
        "study_context": True,
        "session_clock": True,
        "research_csv_logger": True,
    }
    status = "ABORTED"
    abort_reason: str | None = None
    report: dict[str, Json] = {}
    runtime_created = False
    runtime_creation_attempted = False
    stage_started = False
    runtime_cleanup_status = "NOT_CREATED"
    runtime_failure: RuntimeError | ValueError | OSError | KeyboardInterrupt | None = (
        None
    )
    exit_runtime_binding = (
        "DISABLED" if runner.config.exit_assistance is None else "NOT_CREATED"
    )
    exit_status: str | None = None
    exit_terminal_event: str | None = None
    exit_outcome: str | None = None
    exit_termination_reason: str | None = None
    exit_noa_state_t0: str | None = None
    exit_decision: str | None = None
    shutdown_brake: dict[str, Json] = ShutdownBrakeOutcome.not_attempted().manifest()
    try:
        services.emit(
            "p8_assignment_provenance",
            {"source": assignment_source, "reported_history": reported_history},
        )
        services.emit("p8_dev_live_start_attempt", {"sources": sources})
        runtime_creation_attempted = True
        with runner.session() as runtime:
            runtime_created = True
            runtime_cleanup_status = "PENDING"
            persistence = runtime.persistence
            try:
                if persistence is None:
                    raise P8Error("P8_LIVE_PERSISTENCE_MISSING")
                if (
                    persistence.study_run is not services.context
                    or persistence.logger is not services.logger
                ):
                    raise P8Error("P8_LIVE_SHARED_RESOURCE_IDENTITY_MISMATCH")
                if runner.config.exit_assistance is not None:
                    if (
                        runtime.exit_assistance is None
                        or runtime.exit_view_binding is None
                    ):
                        raise P8Error("P8_LIVE_P5_RUNTIME_BINDING_MISSING")
                    exit_runtime_binding = "BOUND"
                services.emit("p8_dev_live_runtime_created", {"shared_objects": shared})
                smoke = runtime.run()
                if smoke is None:
                    raise P8Error("P8_LIVE_REPORT_MISSING")
                report = {
                    "map_name": smoke.map_name,
                    "vehicle_id": smoke.vehicle_id,
                    "vehicle_type": smoke.vehicle_type,
                    "requested_duration_s": smoke.duration,
                    "elapsed_seconds": smoke.elapsed_seconds,
                    "scheduler_updates": smoke.scheduler_updates,
                    "control_frames": smoke.control_frames,
                    "user_exited": smoke.user_exited,
                    "maximum_observed_speed_kmh": smoke.maximum_observed_speed_kmh,
                    "final_control_mode": smoke.final_control_mode.value,
                }
            except KeyboardInterrupt as error:
                runtime_failure = error
                raise
            except (RuntimeError, ValueError, OSError) as error:
                runtime_failure = error
                raise
            finally:
                shutdown_brake = runtime.shutdown_brake_outcome.manifest()
                stage_started = (
                    False if persistence is None else persistence.stage_started
                )
                if runtime.exit_assistance is not None:
                    exit_status = runtime.exit_assistance.status.value
                    for event in reversed(runtime.exit_assistance.events):
                        payload = json.loads(event.payload_json)
                        if payload["outcome"] is not None:
                            exit_terminal_event = event.event_type
                            exit_outcome = payload["outcome"]
                            exit_termination_reason = payload["termination_reason"]
                            exit_noa_state_t0 = payload["noa_state_t0"]
                            exit_decision = payload["decision"]
                            break
        runtime_cleanup_status = "CLOSED"
        services.emit(
            "p8_dev_live_end",
            {"reason": "USER_EXIT" if smoke.user_exited else "TIME_CAP"},
        )
        status = "DEV_LIVE_PARTIAL_COMPLETE"
    except KeyboardInterrupt as error:
        status = "ABORTED"
        abort_reason = "OPERATOR_INTERRUPT"
        if runtime_created and runtime_cleanup_status != "CLOSED":
            runtime_cleanup_status = "CLOSED" if runtime_failure is error else "FAILED"
        if not runtime_created and runtime_creation_attempted:
            runtime_cleanup_status = "UNKNOWN"
        try:
            services.emit(
                "p8_dev_live_abort",
                {"reason": abort_reason, "stage_started": stage_started},
            )
        except (RuntimeError, ValueError, OSError) as emit_error:
            abort_reason += (
                f";ABORT_EVENT_FAILURE:{type(emit_error).__name__}:{emit_error}"
            )
    except (RuntimeError, ValueError, OSError) as error:
        status = "ABORTED"
        abort_reason = f"FAILURE:{type(error).__name__}:{error}"
        if runtime_created and runtime_cleanup_status != "CLOSED":
            runtime_cleanup_status = "CLOSED" if runtime_failure is error else "FAILED"
        if not runtime_created and runtime_creation_attempted:
            runtime_cleanup_status = "UNKNOWN"
        try:
            services.emit(
                "p8_dev_live_abort",
                {"reason": abort_reason, "stage_started": stage_started},
            )
        except (RuntimeError, ValueError, OSError) as emit_error:
            abort_reason += (
                f";ABORT_EVENT_FAILURE:{type(emit_error).__name__}:{emit_error}"
            )

    logger_status = "CLOSED"
    try:
        services.close()
    except (RuntimeError, ValueError, OSError) as error:
        status = "ABORTED"
        logger_status = "FAILED"
        logger_reason = f"LOGGER_CLOSE_FAILURE:{type(error).__name__}:{error}"
        abort_reason = (
            logger_reason if abort_reason is None else f"{abort_reason};{logger_reason}"
        )
    try:
        artifacts = inspect_outputs(
            services.directory,
            services.context,
            complete=False,
            components={},
            provenance=services.provenance,
        )
    except (OSError, ValueError, KeyError) as error:
        status = "ABORTED"
        inspection_reason = f"OUTPUT_INSPECTION_FAILURE:{type(error).__name__}:{error}"
        abort_reason = (
            inspection_reason
            if abort_reason is None
            else f"{abort_reason};{inspection_reason}"
        )
        artifacts = {
            "files": [],
            "issues": [inspection_reason],
            "software_artifacts_complete": False,
            "physical_completeness": "NOT_HARDWARE_VALIDATED",
        }
    result: dict[str, Json] = {
        **services.manifest_base(),
        "completion_status": status,
        "abort_reason": abort_reason,
        "module_1_completed": False,
        "stage": Stage.MODULE_1_RUNNING.value,
        "stage_outcome": "PARTIAL_SEGMENT_ONLY",
        "runtime_state": {
            "runtime_created": runtime_created,
            "stage_started": stage_started,
        },
        "shared_objects": shared,
        "sources": sources,
        "p5_exit": {
            "configured": runner.config.exit_assistance is not None,
            "runtime_binding": exit_runtime_binding,
            "manifest_path": None
            if runner.config.exit_assistance is None
            else str(runner.config.exit_assistance.manifest_path),
            "route_id": None
            if runner.config.exit_assistance is None
            else runner.config.exit_assistance.route_id,
            "lc_event_id": None
            if runner.config.exit_assistance is None
            else runner.config.exit_assistance.lc_event_id,
            "development_validation": runner.config.development_validation,
            "coordinator_status": exit_status,
            "terminal_event": exit_terminal_event,
            "physical_outcome": exit_outcome,
            "termination_reason": exit_termination_reason,
            "noa_state_t0": exit_noa_state_t0,
            "decision": exit_decision,
        },
        "control_config": asdict(runner.config.control_config),
        "speed_policy": runner.config.effective_speed_policy.manifest(
            runner.config.control_config
        ),
        "shutdown_brake": shutdown_brake,
        "runtime_report": report,
        "reported_history": reported_history,
        "partial_artifacts": artifacts,
        "cleanup": {
            "logger_closed": services.closed,
            "logger_status": logger_status,
            "runtime_owned_hero_and_viewer": runtime_cleanup_status,
            "camera_sensor_destruction": "UNVERIFIED",
            "external_ego_destroyed": False,
        },
    }
    services.save_manifest_once(result)
    return result
