from __future__ import annotations

from src.experiment.context import ExperimentModule
from src.experiment.exit_assistance import ExitContext
from src.p8.model import Mode, P8Error, Stage
from src.p8.services import SessionServices
from src.scenario.research_noa_config import ResearchNoARunMode
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.vehicle.carla_exit_route import load_exit_route_manifest


def validate_live_drive(services: SessionServices, runner: ResearchNoARunner) -> None:
    if services.mode is not Mode.DEV_LIVE_DRIVE:
        raise P8Error("P8_LIVE_MODE_REQUIRED")
    if services.stage is not Stage.SESSION_CREATED:
        raise P8Error("P8_LIVE_SESSION_STAGE_REQUIRED")
    binding = runner.borrowed_log
    if (
        binding is None
        or binding.session_clock is not services.clock
        or binding.study_run is not services.context
        or binding.logger is not services.logger
    ):
        raise P8Error("P8_LIVE_SHARED_RESOURCE_IDENTITY_MISMATCH")
    config = runner.config
    persistence = config.persistence
    interaction = config.automation_interaction
    if (
        config.mode is not ResearchNoARunMode.LIVE_SMOKE
        or persistence is None
        or persistence.assignment != services.assignment
        or persistence.run_id != services.context.study_run_id
        or persistence.output_dir != services.directory
        or persistence.module is not ExperimentModule.MODULE_1
        or interaction is None
        or interaction.module is not ExperimentModule.MODULE_1
        or interaction.condition is not services.context.module_1_condition
        or config.p6 is not None
    ):
        raise P8Error("P8_LIVE_RUNNER_CONFIG_MISMATCH")
    exit_config = config.exit_assistance
    if exit_config is None:
        if config.development_validation:
            raise P8Error("P8_LIVE_RUNNER_CONFIG_MISMATCH")
    else:
        expected_event_id = (
            f"{services.context.study_run_id}:MODULE_1:{exit_config.route_id}"
        )
        if (
            exit_config.route_id != services.assignment.route_id
            or exit_config.lc_event_id != expected_event_id
            or exit_config.context is not ExitContext.MODULE
            or exit_config.confirm_key != "c"
            or exit_config.reject_key != "r"
            or interaction.noa_key != "n"
            or persistence.stage_label != "P8_DEV_LIVE_MODULE_1_PARTIAL"
        ):
            raise P8Error("P8_LIVE_RUNNER_CONFIG_MISMATCH")
        manifest = load_exit_route_manifest(exit_config.manifest_path)
        manifest.route(exit_config.route_id)
    expected_provenance = (
        "REAL_CARLA_RUNTIME_DEV_PARTIAL"
        if runner.client_source == "REAL_CARLA_RPC"
        and runner.driver_input_source == "REAL_KEYBOARD_PATH"
        else "DEV_STUB"
    )
    if services.provenance != expected_provenance:
        raise P8Error("P8_LIVE_PROVENANCE_MISMATCH")
