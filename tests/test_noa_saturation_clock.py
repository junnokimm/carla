from __future__ import annotations

import csv
from itertools import count
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.experiment.noa_control import NoAControlCommand
from src.experiment.session_clock import SessionClockError
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.research_event import ResearchEvent
from src.p8.assignment import development_assignment
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config, run_live_drive
from src.p8.model import Mode
from src.p8.services import SessionServices
from src.scenario.research_noa_persistence import (
    PendingResearchEvent,
    ResearchNoAObservationSource,
    ResearchNoAPersistence,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_speed_policy import ResearchSpeedPolicy
from src.vehicle import VehicleState
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryAdapter
from src.vehicle.carla_noa_simulation_control import CarlaNoAControlStep
from tests.research_noa_fakes import FakeClient, FakeResearchVehicle, FakeResearchWorld
from tests.test_p8_live_drive import InteractiveViewerFactory


@pytest.mark.parametrize("pi_trace", [False, True])
def test_activation_saturation_preserves_clock_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pi_trace: bool
) -> None:
    # Given one strictly increasing clock shared by P8 and Research NoA.
    clock = count(1_000_000_000, 1_000_000).__next__
    monkeypatch.setattr("src.p8.services.time.monotonic_ns", clock)
    services = SessionServices(
        tmp_path, development_assignment(), "activation", Mode.DEV_LIVE_DRIVE
    )
    vehicle = FakeResearchVehicle()
    viewers = InteractiveViewerFactory()
    inputs = iter(
        (
            DriverInput(activation_requested=True),
            DriverInput(deactivation_requested=True),
            DriverInput(activation_requested=True),
            DriverInput(),
        )
    )
    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(
                duration=1.0,
                spawn_index=0,
                noa_desired_speed_kmh=10.0,
                noa_max_throttle=1.0,
                speed_policy=ResearchSpeedPolicy(30.0, True),
                pi_trace=pi_trace,
            ),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewers,
        driver_input_source=inputs.__next__,
        monotonic_ns=clock,
        borrowed_log=services.borrowed_research_log(),
    )

    # When activation, PI control, OFF and reactivation traverse the real runner.
    result = run_live_drive(services, runner, reported_history={})

    # Then neither tracing nor deferred automation events regress the shared clock.
    assert result["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE", result[
        "abort_reason"
    ]
    assert result["runtime_report"]["control_frames"] == 3
    assert result["shutdown_brake"]["command_call_outcome"] == "RETURNED"
    assert result["cleanup"]["runtime_owned_hero_and_viewer"] == "CLOSED"
    assert services.closed
    assert vehicle.destroy_count == viewers.created[0].close_count == 1
    with services.logger.event_path.open(newline="") as source:
        events = list(csv.DictReader(source))
    with services.logger.telemetry_path.open(newline="") as source:
        telemetry = list(csv.DictReader(source))
    assert telemetry
    active = [
        row for row in events
        if row["event_type"] == "auto_state"
        and row["event_value"] == "AVAILABLE|NOA_ACTIVE"
    ]
    assert len(active) == 2
    saturation = [
        row for row in events
        if row["event_type"] == "noa_control_saturation_transition"
    ]
    assert len(saturation) == int(pi_trace)
    if pi_trace:
        assert int(active[0]["host_monotonic_ns"]) < int(
            saturation[0]["host_monotonic_ns"]
        )
        assert events.index(active[0]) < events.index(saturation[0])
    for rows in (events, telemetry):
        timestamps = [int(row["host_monotonic_ns"]) for row in rows]
        assert timestamps == sorted(timestamps)
        for row in rows:
            expected = (int(row["host_monotonic_ns"]) - 1_000_000_000) / 1e9
            assert float(row["session_elapsed_s"]) == expected


@pytest.mark.parametrize("saturation_ns", [1_999_999_999, 2_000_000_000, 3_000_000_000])
def test_saturation_drain_preserves_occurrence_time_and_rejects_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saturation_ns: int
) -> None:
    # Given pending equal-time input events and a separately timestamped PI step.
    monkeypatch.setattr("src.p8.services.time.monotonic_ns", lambda: 1_000_000_000)
    services = SessionServices(
        tmp_path, development_assignment(), "drain", Mode.DEV_LIVE_DRIVE
    )
    config = build_live_drive_config(
        services, LiveDriveOptions(duration=1.0, spawn_index=0)
    )
    assert config.persistence is not None
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    source = ResearchNoAObservationSource(
        world, vehicle, monotonic_ns=iter((saturation_ns,)).__next__,
        utc_ns=lambda: 99,
    )
    persistence = ResearchNoAPersistence(config.persistence, services.logger, source)
    input_timestamp = TimestampEnvelope(HostClockTimestamp(2_000_000_000, 98))
    for name in ("auto_request", "auto_state"):
        persistence.enqueue_event(
            PendingResearchEvent(input_timestamp, ResearchEvent(name))
        )
    step = CarlaNoAControlStep(
        frame=1, simulation_time_seconds=0.1, delta_seconds=0.1,
        target_speed_kmh=10.0, measured_speed_kmh=0.0, integral_effort=0.0,
        lane_geometry=CarlaLaneGeometryAdapter(world.get_map()).observe_with_context(vehicle),
        command=NoAControlCommand(0.4, 0.0, 0.0), throttle_saturated=True,
    )

    # When saturation is recorded, earlier events must drain without resampling time.
    try:
        if saturation_ns < input_timestamp.host.monotonic_ns:
            with pytest.raises(SessionClockError, match="moved backwards"):
                persistence.record_control_saturation(step)
        else:
            persistence.record_control_saturation(step)
            persistence.flush_automation_events()
            services.logger.write_telemetry(
                persistence.segment,
                TimestampEnvelope(HostClockTimestamp(saturation_ns, 99)),
                VehicleState(0.1, 0.0, 0.0, 0.4, 0.0, None, "off"),
            )
            with pytest.raises(SessionClockError, match="moved backwards"):
                services.clock.elapsed_seconds(HostClockTimestamp(saturation_ns - 1, 100))
    finally:
        services.close()

    # Then CSV values retain their original host clocks and stable equal-time order.
    with services.logger.event_path.open(newline="") as output:
        rows = list(csv.DictReader(output))
    assert [row["event_type"] for row in rows[:2]] == ["auto_request", "auto_state"]
    assert [int(row["host_monotonic_ns"]) for row in rows[:2]] == [2_000_000_000] * 2
    if saturation_ns >= input_timestamp.host.monotonic_ns:
        assert len(rows) == 3
        assert rows[2]["event_type"] == "noa_control_saturation_transition"
        assert int(rows[2]["host_monotonic_ns"]) == saturation_ns
        assert rows[2]["host_utc_ns"] == "99"
        with services.logger.telemetry_path.open(newline="") as output:
            telemetry = next(csv.DictReader(output))
        assert telemetry["session_elapsed_s"] == rows[2]["session_elapsed_s"]
    else:
        assert len(rows) == 2
