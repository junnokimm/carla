from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, Stage
from src.p8.native_tm_diagnostics_types import NativeTrafficManagerDiagnosticsStatus
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import RegistrationRequest
from src.scenario.driver_view import DriverViewConfig
from src.scenario.research_noa import ResearchNoARunConfig, ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_noa_runtime import make_control_config
from tests.test_p8_operator_backend import (
    RunnerEvidence,
    StrictClock,
    advance_to,
    runtime_config,
)


class OperatorCollector:
    def __init__(self) -> None:
        self.interruption: KeyboardInterrupt | None = None
        self.close_calls = 0
        self.closed = False

    def mark_interrupted(self, error: KeyboardInterrupt) -> None:
        self.interruption = error

    def close(self) -> None:
        if self.closed:
            return
        self.close_calls += 1
        self.closed = True
        if self.interruption is not None:
            raise self.interruption

    @property
    def status(self) -> NativeTrafficManagerDiagnosticsStatus:
        interrupted = self.interruption is not None
        return NativeTrafficManagerDiagnosticsStatus(
            "diagnostic.jsonl",
            42,
            "MODULE_1_RUNNING",
            "MODULE_1",
            1,
            "FAILED_OBSERVER" if interrupted else "COMPLETE",
            not interrupted,
            0,
            0,
            0,
            0,
            "KeyboardInterrupt:operator interrupt" if interrupted else None,
        )


def test_viewer_close_interrupt_still_destroys_owned_vehicle() -> None:
    class InterruptingCloseView(FakeDriverView):
        def close(self) -> None:
            self.close_count += 1
            raise KeyboardInterrupt("viewer close interrupted")

    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)

    def viewer_factory(
        world_arg: FakeResearchWorld,
        hero: FakeResearchVehicle,
        config: DriverViewConfig,
    ) -> InterruptingCloseView:
        assert world_arg is world
        assert hero is vehicle
        return InterruptingCloseView(config)

    runner = ResearchNoARunner(
        ResearchNoARunConfig(duration=1.0, control_config=make_control_config()),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with (
        pytest.raises(KeyboardInterrupt, match="viewer close interrupted"),
        runner.session(),
    ):
        pass

    assert vehicle.destroy_count == 1


def test_operator_caught_interrupt_marks_collector_before_session_cleanup(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    evidence = RunnerEvidence([], [], [], [])
    collector = OperatorCollector()
    base_runtime = runtime_config(clock, evidence)
    original_builder = base_runtime.runner_builder

    def build(config, borrowed_log):
        runner = original_builder(config, borrowed_log)
        original_create = runner._create_session

        def create(*args, **kwargs):
            session = original_create(*args, **kwargs)
            session.native_tm_collector = collector
            return session

        runner._create_session = create
        return runner

    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=replace(base_runtime, runner_builder=build),
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            participant_id="R-0042",
            run_id="operator-interrupt",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=Module2Condition.MANUAL,
        )
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    interrupt = KeyboardInterrupt("operator interrupt")
    evidence.failure = interrupt

    with pytest.raises(KeyboardInterrupt) as caught:
        controller.execute_current_module(1.0)

    assert caught.value is interrupt
    assert collector.interruption is interrupt
    assert collector.close_calls == 1
    assert collector.status.coverage_status == "FAILED_OBSERVER"
