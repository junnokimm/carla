from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import carla
import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, P8Error, Stage
from src.p8.operator_cli import _runtime_config, build_parser, main
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import DevOutcome, RegistrationRequest, TrainingRun
from src.scenario.research_noa_config import ResearchNoARunConfig
from src.scenario.research_noa_log_setup import OpenResearchLog
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_speed_policy import ResearchSpeedPolicy
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p8_operator_backend import (
    RunnerEvidence,
    StrictClock,
    advance_to,
    runtime_config,
)

IDENTITY_ARGS = [
    "--participant-id",
    "SPEED-TEST",
    "--run-id",
    "speed-test",
    "--m1",
    "NO_SURT",
    "--m2",
    "MANUAL",
    "--ack-dev-live",
    "--spawn-index",
    "0",
]
SPEED_ARGS: list[str] = []


class TrafficManagerLifecycleVehicle(FakeResearchVehicle):
    def destroy(self) -> bool:
        self.events.append("destroy")
        return super().destroy()


def test_operator_requires_no_custom_speed_or_throttle_setting(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.p8.operator_cli.run_operator_ui", lambda *_: calls.append("ui") or 0
    )

    assert main(IDENTITY_ARGS) == 0
    assert calls == ["ui"]


def test_operator_cli_uses_tm_default_speed_and_no_custom_target() -> None:
    runtime = _runtime_config(build_parser().parse_args([*IDENTITY_ARGS, *SPEED_ARGS]))

    assert runtime.speed_policy is None
    assert runtime.traffic_manager_port == 8000
    assert runtime.control_config.longitudinal.target_speed_kmh is None
    assert runtime.control_config.longitudinal.max_throttle == 1.0
    assert runtime.control_config.longitudinal.speed_deadband_kmh == 0.2
    assert runtime.control_config.longitudinal.acceleration_gain == 0.1
    assert runtime.control_config.longitudinal.braking_gain == 0.1
    assert runtime.control_config.longitudinal.integral_gain == 0.02
    assert runtime.control_config.longitudinal.max_brake == 0.5
    assert runtime.control_config.lateral.max_steering == 0.15


def test_operator_rejects_legacy_custom_policy_at_config_boundary() -> None:
    base = runtime_config(StrictClock(), RunnerEvidence([], [], [], []))

    with pytest.raises((P8Error, ValueError), match="speed_policy"):
        replace(base, speed_policy=ResearchSpeedPolicy(20.0, noa_only=False))


@pytest.mark.parametrize(
    "override",
    [
        ["--noa-max-throttle", "0"],
        ["--noa-max-throttle", "1.01"],
    ],
)
def test_invalid_explicit_policy_fails_before_ui(
    monkeypatch, override: list[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "src.p8.operator_cli.run_operator_ui", lambda *_: calls.append("ui")
    )

    with pytest.raises(SystemExit) as error:
        main([*IDENTITY_ARGS, *SPEED_ARGS, *override])

    assert error.value.code == 2
    assert calls == []


@pytest.mark.parametrize("m2", list(Module2Condition))
def test_three_stages_borrow_same_explicit_policy_and_log_real_speed(
    tmp_path: Path, m2: Module2Condition
) -> None:
    clock = StrictClock()
    runtime = _runtime_config(build_parser().parse_args([*IDENTITY_ARGS, *SPEED_ARGS]))
    configs: list[ResearchNoARunConfig] = []
    logs: list[OpenResearchLog] = []
    vehicles: list[FakeResearchVehicle] = []
    viewers: list[FakeDriverView] = []

    def build(
        config: ResearchNoARunConfig, borrowed: OpenResearchLog
    ) -> ResearchNoARunner:
        configs.append(config)
        logs.append(borrowed)
        vehicle = TrafficManagerLifecycleVehicle()
        vehicles.append(vehicle)
        run_clock = FakeMonotonicClock()
        world = FakeResearchWorld(vehicle, monotonic_clock=run_clock)

        def viewer_factory(world_arg, hero, view_config):
            viewer = FakeDriverView(view_config)

            def drive() -> None:
                vehicle.velocity = carla.Vector3D(x=100.0 / 3.6)
                vehicle.speed_limit_kmh = 80.0
                run_clock.advance(config.duration / 2.0)

            def changed_limit() -> None:
                vehicle.speed_limit_kmh = 100.0
                run_clock.advance(config.duration / 2.0)

            viewer.actions = [drive, changed_limit]
            viewers.append(viewer)
            return viewer

        return ResearchNoARunner(
            config,
            client=FakeClient(world),
            viewer_factory=viewer_factory,
            monotonic_clock=run_clock,
            monotonic_ns=clock.monotonic_ns,
            utc_ns=clock.utc_ns,
            borrowed_log=borrowed,
        )

    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=replace(runtime, runner_builder=build),
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            "SPEED-TEST",
            "speed-test",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=m2,
        )
    )
    try:
        advance_to(controller, Stage.TRAINING)
        controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)
        advance_to(controller, Stage.MODULE_1_RUNNING)
        controller.execute_current_module(60.0)
        controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "bounded test")
        controller.advance()
        controller.advance()
        clock.advance(120.0)
        controller.advance()
        controller.advance()
        controller.execute_current_module(60.0)

        assert len(configs) == 3
        assert all(item.speed_policy is None for item in configs)
        assert all(item.traffic_manager_port == 8000 for item in configs)
        assert all(item.control_config is runtime.control_config for item in configs)
        assert [item.duration for item in configs] == [600.0, 60.0, 60.0]
        assert [item.operator_training for item in configs] == [True, False, False]
        assert all(item.logger is controller.session.services.logger for item in logs)
        assert all(
            item.session_clock is controller.session.services.clock for item in logs
        )
        assert [item.destroy_count for item in vehicles] == [1, 1, 1]
        assert all(item.events[-1] == "destroy" for item in vehicles)
        assert all(
            max(
                index
                for index, event in enumerate(item.events)
                if event == "autopilot:False"
            )
            < item.events.index("destroy")
            for item in vehicles
        )
        expected_m2_registration = m2 is Module2Condition.NOA_L2
        assert (
            any(
                enabled
                for enabled, port in vehicles[-1].autopilot_port_calls
                if port == 8000
            )
            is expected_m2_registration
        )
        assert [item.close_count for item in viewers] == [1, 1, 1]
        assert all(
            item.config.manual_control_limits.max_throttle == 1.0 for item in viewers
        )
        assert controller.snapshot().module_runs[-1].initial_mode == (
            "MANUAL" if m2 is Module2Condition.MANUAL else "NOA_ACTIVE"
        )
        with controller.session.services.logger.event_path.open(
            newline="", encoding="utf-8"
        ) as source:
            policies = [
                json.loads(row["event_value"])
                for row in csv.DictReader(source)
                if row["event_type"] == "control_authority"
            ]
        assert len(policies) == 3
        assert all(item["target_speed_kmh"] is None for item in policies)
        assert all(
            item["speed_control"] == "TRAFFIC_MANAGER_DEFAULT" for item in policies
        )
        assert all(
            item["configured_automation_provider"] == "CARLA_TRAFFIC_MANAGER"
            for item in policies
        )
        assert all(item["current_authority"] == "DRIVER_MANUAL" for item in policies)
        assert all(item["control_mode"] == "MANUAL" for item in policies)
        with controller.session.services.logger.event_path.open(
            newline="", encoding="utf-8"
        ) as source:
            speed_limits = [
                json.loads(row["event_value"])
                for row in csv.DictReader(source)
                if row["event_type"] == "traffic_manager_speed_limit_observation"
            ]
        assert {item["observed_speed_limit_kmh"] for item in speed_limits} == {
            80.0,
            100.0,
        }
        assert all(
            item["actual_speed_kmh"] == pytest.approx(100.0) for item in speed_limits
        )
        assert all(
            item["configured_automation_provider"] == "CARLA_TRAFFIC_MANAGER"
            for item in speed_limits
        )
        assert all(
            item["source"] == "HOST_RPC_CACHED_SPEED_LIMIT" for item in speed_limits
        )
        with controller.session.services.logger.telemetry_path.open(
            newline="", encoding="utf-8"
        ) as source:
            telemetry = list(csv.DictReader(source))
        assert max(float(row["speed_kmh"]) for row in telemetry) == pytest.approx(100.0)
        assert {row["run_id"] for row in telemetry} == {"speed-test"}
        assert {row["participant_id"] for row in telemetry} == {"SPEED-TEST"}
    finally:
        controller.abort("TEST_TEARDOWN")


def test_integrated_training_manual_mode_has_no_numeric_speed_cap(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    evidence = RunnerEvidence([], [], [], [])
    base = runtime_config(clock, evidence)

    def build(
        config: ResearchNoARunConfig, borrowed: OpenResearchLog
    ) -> ResearchNoARunner:
        runner = base.runner_builder(config, borrowed)
        evidence.vehicles[-1].velocity = carla.Vector3D(x=111.0 / 3.6)
        return runner

    runtime = _runtime_config(build_parser().parse_args([*IDENTITY_ARGS, *SPEED_ARGS]))
    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=replace(runtime, runner_builder=build),
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            "SPEED-TEST",
            "speed-test",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=Module2Condition.MANUAL,
        )
    )
    advance_to(controller, Stage.TRAINING)

    result = controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)

    assert result.user_exited is False
    assert controller.session.result["completion_status"] == "RUNNING"
    assert not controller.session.services.closed
    assert evidence.vehicles[0].destroy_count == 1
    assert evidence.viewers[0].close_count == 1
