from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.noa_control import NoAControlCommand
from src.logging.csv_logger import RESEARCH_COMMON_HEADER
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config, run_live_drive
from src.p8.model import Mode
from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from src.scenario.research_noa_speed_policy import ResearchSpeedPolicy
from src.vehicle.carla_noa_simulation_control import (
    CarlaNoACachedControlReadback,
    CarlaNoAControlStep,
)
from tests.lateral_validation_fakes import make_geometry_context
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchWorld,
)
from tests.test_p8_live_drive import _services
from tests.test_research_noa_live_safety import SequencedResearchVehicle


def _high_speed_runner(
    tmp_path: Path,
    run_id: str,
    speeds_kmh: list[float],
    inputs: Iterator[DriverInput],
    *,
    frame_count: int,
    traces: list[CarlaNoAControlStep] | None = None,
    pi_trace: bool = False,
) -> tuple[
    SessionServices,
    ResearchNoARunner,
    SequencedResearchVehicle,
    FakeResearchWorld,
]:
    services = _services(tmp_path, run_id)
    vehicle = SequencedResearchVehicle(speeds_kmh)
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()

    def configured_viewer_factory(world, hero, config):
        viewer = viewer_factory(world, hero, config)
        viewer.actions = [lambda: None] * frame_count
        return viewer

    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(
                duration=1.0,
                spawn_index=0,
                noa_desired_speed_kmh=25.0,
                speed_policy=ResearchSpeedPolicy(35.0, True),
                noa_max_throttle=1.0,
                pi_trace=pi_trace,
            ),
        ),
        client=FakeClient(world),
        viewer_factory=configured_viewer_factory,
        driver_input_source=inputs.__next__,
        control_trace=None if traces is None else traces.append,
        borrowed_log=services.borrowed_research_log(),
    )
    return services, runner, vehicle, world


@pytest.mark.parametrize(
    ("source", "speeds_kmh", "inputs", "frame_count"),
    [
        ("INITIAL", [36.0, 7.0], iter(()), 0),
        ("MANUAL", [0.0, 0.0, 36.0, 7.0], iter((DriverInput(),)), 1),
        (
            "NOA_ACTIVE",
            [0.0, 0.0, 36.0, 7.0],
            iter((DriverInput(activation_requested=True),)),
            1,
        ),
        (
            "FINAL",
            [0.0, 25.0, 25.0, 25.0, 36.0, 7.0],
            iter((DriverInput(),)),
            1,
        ),
    ],
)
def test_high_speed_profile_rejects_36_at_every_runtime_boundary(
    tmp_path: Path,
    source: str,
    speeds_kmh: list[float],
    inputs: Iterator[DriverInput],
    frame_count: int,
) -> None:
    services, runner, _, _ = _high_speed_runner(
        tmp_path,
        f"high-{source.lower()}",
        speeds_kmh,
        inputs,
        frame_count=frame_count,
    )
    with (
        runner.session() as session,
        pytest.raises(ResearchNoAActualSpeedSafetyError) as caught,
    ):
        session.run()

    assert caught.value.source == source
    assert caught.value.actual_speed_kmh == pytest.approx(36.0)
    assert caught.value.hard_limit_kmh == pytest.approx(35.0)
    services.close()


@pytest.mark.parametrize(
    "driver_input",
    [DriverInput(), DriverInput(activation_requested=True)],
    ids=["manual", "noa-active"],
)
def test_high_speed_profile_accepts_25_and_reaches_real_backend_trace(
    tmp_path: Path,
    driver_input: DriverInput,
) -> None:
    traces: list[CarlaNoAControlStep] = []
    services, runner, _, _ = _high_speed_runner(
        tmp_path,
        f"high-accepted-{driver_input.activation_requested}",
        [0.0, 25.0, 25.0, 25.0, 25.0],
        iter((driver_input,)),
        frame_count=1,
        traces=traces,
    )
    report = runner.run()

    assert report is not None
    assert report.target_speed_kmh == pytest.approx(25.0)
    assert report.maximum_observed_speed_kmh == pytest.approx(25.0)
    if driver_input.activation_requested:
        assert [step.target_speed_kmh for step in traces] == [25.0]
    else:
        assert traces == []
    services.close()


@pytest.mark.parametrize(
    ("measured_speed", "expected_throttle", "saturated"),
    [(0.0, 1.0, True), (24.0, 0.08, False)],
)
def test_high_cap_is_a_maximum_and_real_backend_computes_commands(
    tmp_path: Path,
    measured_speed: float,
    expected_throttle: float,
    saturated: bool,
) -> None:
    traces: list[CarlaNoAControlStep] = []
    services, runner, vehicle, _ = _high_speed_runner(
        tmp_path,
        "high-cap-command",
        [0.0, measured_speed, measured_speed, measured_speed, measured_speed],
        iter((DriverInput(activation_requested=True),)),
        frame_count=1,
        traces=traces,
    )

    report = runner.run()

    assert report is not None
    assert traces[0].command.throttle == pytest.approx(expected_throttle)
    assert traces[0].command.brake == 0.0
    assert traces[0].throttle_saturated is saturated
    assert traces[0].target_speed_kmh == pytest.approx(25.0)
    assert traces[0].cached_readback is not None
    assert traces[0].cached_readback.gear == 1
    assert traces[0].cached_readback.manual_gear_shift is False
    assert vehicle.get_control_count > 0
    services.close()


def test_pi_trace_coexists_with_callback_without_extra_control_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_traces: list[CarlaNoAControlStep] = []
    baseline_services, baseline_runner, baseline_vehicle, _ = _high_speed_runner(
        tmp_path,
        "trace-read-baseline",
        [0.0, 0.0, 0.0, 0.0, 0.0],
        iter((DriverInput(activation_requested=True),)),
        frame_count=1,
        traces=baseline_traces,
    )
    baseline_runner.run()
    baseline_reads = baseline_vehicle.get_control_count
    baseline_services.close()

    traces: list[CarlaNoAControlStep] = []
    services, runner, vehicle, _ = _high_speed_runner(
        tmp_path,
        "trace-read-enabled",
        [0.0, 0.0, 0.0, 0.0, 0.0],
        iter((DriverInput(activation_requested=True),)),
        frame_count=1,
        traces=traces,
        pi_trace=True,
    )
    runner.run()
    services.close()

    assert len(traces) == 1
    assert traces[0].cached_readback is not None
    assert vehicle.get_control_count == baseline_reads
    assert "cached_readback_throttle" in capsys.readouterr().out
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        transition = next(
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "noa_control_saturation_transition"
        )
    payload = json.loads(transition["event_value"])
    assert transition["study_run_id"] == "trace-read-enabled"
    assert transition["run_id"] == "trace-read-enabled"
    assert transition["session_elapsed_s"]
    assert transition["host_monotonic_ns"]
    assert payload["transition"] == "INITIAL"
    assert payload["saturated"]["throttle"] is True
    assert payload["commanded"]["throttle"] == pytest.approx(1.0)
    assert payload["cached_readback"] == {
        "semantics": "CACHED_GET_CONTROL_NOT_PHYSICAL_APPLICATION_PROOF",
        "throttle": pytest.approx(1.0),
        "brake": pytest.approx(0.0),
        "steering": pytest.approx(0.0),
        "gear": 1,
        "manual_gear_shift": False,
    }


def test_saturation_events_emit_initial_change_and_clear_only(tmp_path: Path) -> None:
    services, runner, _, _ = _high_speed_runner(
        tmp_path,
        "saturation-transitions",
        [0.0],
        iter(()),
        frame_count=0,
        pi_trace=True,
    )
    readback = CarlaNoACachedControlReadback(1.0, 0.0, 0.0, 1, False)
    saturated = CarlaNoAControlStep(
        1,
        0.05,
        0.05,
        25.0,
        0.0,
        0.0,
        make_geometry_context(),
        NoAControlCommand(1.0, 0.0, 0.0),
        throttle_saturated=True,
        cached_readback=readback,
    )
    clear = CarlaNoAControlStep(
        2,
        0.1,
        0.05,
        25.0,
        24.0,
        0.0,
        make_geometry_context(),
        NoAControlCommand(0.1, 0.0, 0.0),
        cached_readback=CarlaNoACachedControlReadback(0.1, 0.0, 0.0, 1, False),
    )

    with runner.session() as session:
        assert session.persistence is not None
        session.persistence.record_control_saturation(saturated)
        session.persistence.record_control_saturation(saturated)
        session.persistence.record_control_saturation(clear)
    services.close()

    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        transitions = [
            json.loads(row["event_value"])
            for row in csv.DictReader(source)
            if row["event_type"] == "noa_control_saturation_transition"
        ]
    assert [item["transition"] for item in transitions] == ["INITIAL", "CHANGED"]
    assert transitions[0]["saturated"]["throttle"] is True
    assert transitions[1]["previous"]["throttle"] is True
    assert transitions[1]["saturated"]["throttle"] is False


def test_loop_violation_brakes_before_failing_logger_and_preserves_speed_error(
    tmp_path: Path,
) -> None:
    services, runner, vehicle, _ = _high_speed_runner(
        tmp_path,
        "high-loop-logger",
        [0.0, 0.0, 36.0],
        iter((DriverInput(),)),
        frame_count=1,
    )
    with runner.session() as session:
        assert session.persistence is not None

        def fail_after_observing_brake(*_args, **_kwargs) -> None:
            assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
            raise ValueError("event write failed")

        session.persistence.record_speed_limit_violation = fail_after_observing_brake
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

    assert caught.value.source == "MANUAL"
    services.close()


def test_violation_telemetry_and_event_share_common_timestamp_fields(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "violation-shared-timestamp")
    vehicle = SequencedResearchVehicle([0.0, 0.0, 21.0, 7.0])
    viewer_factory = FakeDriverViewFactory()

    def configured_viewer_factory(world, hero, config):
        viewer = viewer_factory(world, hero, config)
        viewer.actions = [lambda: None]
        return viewer

    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=configured_viewer_factory,
        driver_input_source=iter((DriverInput(),)).__next__,
        borrowed_log=services.borrowed_research_log(),
    )

    result = run_live_drive(services, runner, reported_history={})

    assert result["completion_status"] == "ABORTED"
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        event = next(
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "speed_limit_violation"
        )
    with services.logger.telemetry_path.open(newline="", encoding="utf-8") as source:
        telemetry = next(
            row
            for row in csv.DictReader(source)
            if float(row["speed_kmh"]) == pytest.approx(7.0)
        )
    assert {field: event[field] for field in RESEARCH_COMMON_HEADER} == {
        field: telemetry[field] for field in RESEARCH_COMMON_HEADER
    }
    assert json.loads(event["event_value"])["observation_capture_status"] == (
        "CAPTURED_POST_RESPONSE"
    )


def test_participant_mode_blocks_high_speed_runner_before_spawn(tmp_path: Path) -> None:
    services, runner, _, world = _high_speed_runner(
        tmp_path,
        "high-participant-blocked",
        [0.0],
        iter(()),
        frame_count=0,
    )
    services.mode = Mode.PARTICIPANT
    with pytest.raises(RuntimeError, match="P8_LIVE_MODE_REQUIRED"):
        run_live_drive(services, runner, reported_history={})

    assert world.spawn_calls == []
    services.close()
