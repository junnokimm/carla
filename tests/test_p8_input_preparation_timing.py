from dataclasses import replace

import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, Stage
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import RegistrationRequest, TrainingRun
from src.p8.tsxw_input import TsxwDeviceError
from src.p8.tsxw_policy import TsxwConfig
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManagerRuntimeAttestor,
)
from tests.test_p8_operator_backend import (
    RunnerEvidence,
    StrictClock,
    advance_to,
    runtime_config,
)


@pytest.mark.parametrize("cancel_preparation", [False, True])
def test_training_input_preparation_precedes_active_time_and_cancellation_saves(
    tmp_path, cancel_preparation
):
    host = StrictClock()
    evidence = RunnerEvidence([], [], [], [])
    base = runtime_config(host, evidence)
    run_clock = FakeMonotonicClock()
    vehicle = FakeResearchVehicle(initial_gear=0)
    world = FakeResearchWorld(vehicle, monotonic_clock=run_clock)
    views = []

    class View(FakeDriverView):
        def prepare_input(self):
            assert (
                self.config.automation_state_source().control_mode
                is DrivingControlMode.MANUAL
            )
            assert vehicle.current_control.gear > 0
            assert not vehicle.current_control.manual_gear_shift
            assert vehicle.current_control.brake == pytest.approx(0.5)
            assert not any(enabled for enabled, _ in vehicle.autopilot_port_calls)
            run_clock.advance(25.0)
            host.advance(25.0)
            if cancel_preparation:
                raise TsxwDeviceError("TSXW_INPUT_PREPARATION_CANCELLED")

    def build(config, borrowed):
        def factory(world_arg, hero, view_config):
            view = View(view_config)

            def drive():
                run_clock.advance(config.duration)
                host.advance(config.duration)

            view.actions = [drive]
            views.append(view)
            return view

        return ResearchNoARunner(
            config,
            client=FakeClient(world),
            viewer_factory=factory,
            monotonic_clock=run_clock,
            monotonic_ns=host.monotonic_ns,
            utc_ns=host.utc_ns,
            borrowed_log=borrowed,
            tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
        )

    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=replace(
            base,
            runner_builder=build,
            tsxw_config=TsxwConfig(index=0, steering_deadzone=0.02),
        ),
        monotonic_ns=host.monotonic_ns,
        utc_ns=host.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            "INPUT-PREPARATION",
            "input-preparation",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=Module2Condition.NOA_L2,
        )
    )
    advance_to(controller, Stage.TRAINING)
    try:
        if cancel_preparation:
            with pytest.raises(TsxwDeviceError, match="PREPARATION_CANCELLED"):
                controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)
            assert controller.session.closed
            assert controller.snapshot().stage is Stage.ABORTED
            assert not controller.snapshot().module_runs
            assert views[0].received_scheduler is None
            assert controller.session.result_export.archive.is_file()
        else:
            result = controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)
            assert result.actual_active_drive_s == pytest.approx(600.0)
            assert result.actual_active_start_session_s >= 25.0
            assert result.planned_duration_s == 600.0
        assert vehicle.destroy_count == views[0].close_count == 1
    finally:
        controller.abort("TEST_TEARDOWN")
