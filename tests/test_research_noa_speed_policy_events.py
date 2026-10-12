from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.p7.types import Json
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config, run_live_drive
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchWorld,
)
from tests.test_p8_live_drive import _services
from tests.test_research_noa_live_safety import SequencedResearchVehicle


def _run_speed_sequence(
    tmp_path: Path,
    run_id: str,
    speeds_kmh: list[float],
    inputs: Iterator[DriverInput],
    frame_count: int,
) -> tuple[dict[str, Json], list[dict[str, str]], SequencedResearchVehicle]:
    services = _services(tmp_path, run_id)
    vehicle = SequencedResearchVehicle(speeds_kmh)
    viewer_factory = FakeDriverViewFactory()
    config = build_live_drive_config(
        services,
        LiveDriveOptions(duration=1.0, spawn_index=0),
    )
    configured_viewer_factory = viewer_factory
    if frame_count:
        original_factory = viewer_factory

        def configure_viewer(world, hero, config):
            viewer = original_factory(world, hero, config)
            viewer.actions = [lambda: None] * frame_count
            return viewer

        configured_viewer_factory = configure_viewer
    runner = ResearchNoARunner(
        config,
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=configured_viewer_factory,
        driver_input_source=inputs.__next__,
        borrowed_log=services.borrowed_research_log(),
    )
    result = run_live_drive(services, runner, reported_history={})
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        events = list(csv.DictReader(source))
    return result, events, vehicle


@pytest.mark.parametrize(
    ("source", "speeds_kmh", "inputs", "frame_count"),
    [
        ("INITIAL", [21.0, 7.0], iter(()), 0),
        ("MANUAL", [0.0, 0.0, 21.0, 7.0], iter((DriverInput(),)), 1),
        (
            "NOA_ACTIVE",
            [0.0, 0.0, 21.0, 7.0],
            iter((DriverInput(activation_requested=True),)),
            1,
        ),
        ("FINAL", [0.0, 0.0, 5.0, 5.0, 21.0, 7.0], iter((DriverInput(),)), 1),
    ],
)
def test_speed_limit_violation_is_logged_at_every_live_observation_boundary(
    tmp_path: Path,
    source: str,
    speeds_kmh: list[float],
    inputs: Iterator[DriverInput],
    frame_count: int,
) -> None:
    result, events, vehicle = _run_speed_sequence(
        tmp_path,
        source.lower(),
        speeds_kmh,
        inputs,
        frame_count,
    )

    violation = next(
        row for row in events if row["event_type"] == "speed_limit_violation"
    )
    payload = json.loads(violation["event_value"])
    assert result["completion_status"] == "ABORTED"
    assert payload["source"] == source
    assert payload["triggering_speed_kmh"] == pytest.approx(21.0)
    assert payload["desired_speed_kmh"] == pytest.approx(10.0)
    assert payload["max_allowed_speed_kmh"] == pytest.approx(110.0)
    assert payload["effective_desired_speed_kmh"] == pytest.approx(10.0)
    assert payload["operational_guard_speed_kmh"] == pytest.approx(20.0)
    assert payload["trigger_control_mode"] in {"MANUAL", "NOA_ACTIVE"}
    if source == "INITIAL":
        assert vehicle.applied_controls == []
        assert payload["commanded_response"] == "NO_CONTROL_PRECONDITION"
        assert payload["observation_capture_status"] == "CAPTURED_POST_RESPONSE"
    else:
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
        assert payload["commanded_response"] == "BOUNDED_SHUTDOWN_REQUESTED"
        assert payload["observed_brake"] == pytest.approx(0.5)
        assert payload["observed_throttle"] == pytest.approx(0.0)
        assert payload["observed_steering"] == pytest.approx(0.0)


def test_nonfinite_violation_event_is_strict_json_and_preserves_safety_error(
    tmp_path: Path,
) -> None:
    _, events, _ = _run_speed_sequence(
        tmp_path,
        "nonfinite",
        [float("nan"), 0.0],
        iter(()),
        0,
    )

    violation = next(
        row for row in events if row["event_type"] == "speed_limit_violation"
    )
    payload = json.loads(violation["event_value"])
    assert payload["triggering_speed_kmh"] is None
    assert payload["triggering_speed_reason"] == "NONFINITE"
    assert "NaN" not in violation["event_value"]


def test_speed_policy_event_precedes_stage_start_and_matches_manifest(
    tmp_path: Path,
) -> None:
    result, events, _ = _run_speed_sequence(
        tmp_path,
        "effective-policy",
        [0.0, 0.0, 5.0, 5.0, 5.0],
        iter((DriverInput(),)),
        1,
    )

    event_types = [row["event_type"] for row in events]
    policy_event = next(
        row for row in events if row["event_type"] == "speed_policy_effective"
    )
    assert event_types.index("speed_policy_effective") < event_types.index(
        "stage_start"
    )
    assert json.loads(policy_event["event_value"]) == result["speed_policy"]


def test_manual_speed_peak_is_reported_without_noa_mean_samples(tmp_path: Path) -> None:
    result, _, _ = _run_speed_sequence(
        tmp_path,
        "manual-peak",
        [0.0, 0.0, 15.0, 5.0, 5.0],
        iter((DriverInput(),)),
        1,
    )

    report = result["runtime_report"]
    assert report["maximum_observed_speed_kmh"] == pytest.approx(15.0)
    assert report["control_frames"] == 0


@pytest.mark.parametrize("failure_type", [OSError, ValueError, TypeError])
def test_violation_logger_failure_does_not_replace_safety_error(
    tmp_path: Path,
    failure_type: type[Exception],
) -> None:
    services = _services(tmp_path, f"logger-failure-{failure_type.__name__}")
    vehicle = SequencedResearchVehicle([21.0])
    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=FakeDriverViewFactory(),
        borrowed_log=services.borrowed_research_log(),
    )

    with runner.session() as session:
        assert session.persistence is not None

        def fail_event(*_args, **_kwargs) -> None:
            raise failure_type("event write failed")

        session.persistence.record_speed_limit_violation = fail_event
        with pytest.raises(ResearchNoAActualSpeedSafetyError) as caught:
            session.run()

        assert caught.value.source == "INITIAL"

    assert vehicle.applied_controls == []
    services.close()


def test_violation_event_marks_post_response_capture_unavailable(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "capture-unavailable")
    runner = ResearchNoARunner(
        build_live_drive_config(
            services,
            LiveDriveOptions(duration=1.0, spawn_index=0),
        ),
        client=FakeClient(FakeResearchWorld(SequencedResearchVehicle([21.0]))),
        viewer_factory=FakeDriverViewFactory(),
        borrowed_log=services.borrowed_research_log(),
    )

    with runner.session() as session:
        assert session.persistence is not None

        def fail_capture():
            raise OSError("observation unavailable")

        session.persistence.source.get_observation = fail_capture
        with pytest.raises(ResearchNoAActualSpeedSafetyError):
            session.run()

    services.close()
    with services.logger.event_path.open(newline="", encoding="utf-8") as source:
        violation = next(
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "speed_limit_violation"
        )
    payload = json.loads(violation["event_value"])
    assert payload["observation_capture_status"] == "UNAVAILABLE"
    assert payload["observation_capture_error"] == "OSError"
    assert payload["observed_speed_kmh"] is None
