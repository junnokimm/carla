from __future__ import annotations

import json
from dataclasses import replace

import carla
import pytest

from src.experiment.context import ExperimentModule, Module1Condition
from src.experiment.exit_assistance import (
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitTerminationReason,
)
from src.p8.operator_exit_result import configured_exit_result
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_exit_ledger import reconstruct_exit_ledger
from src.scenario.research_exit_runtime import ResearchExitObservationSource
from tests.test_p5_exit_assistance import (
    MANUAL,
    ReferenceRecorder,
    coordinator,
    observation,
    stamp,
)
from tests.test_p8_operator_p5_results import _live, _write_events
from tests.test_phase2_p5_timing import SnapshotSource, snapshot


def subject() -> ExitAssistanceCoordinator:
    return coordinator(
        ExperimentModule.MODULE_1, Module1Condition.NO_SURT, ReferenceRecorder([])
    )


def present(s: ExitAssistanceCoordinator, obs=None) -> None:
    observed = observation(250) if obs is None else obs
    s.observe(observed)
    s.mark_presented(
        s.prepare_presentation(MANUAL),
        MANUAL,
        stamp(observed.timestamp.host.monotonic_ns + 5),
    )


def payloads(s: ExitAssistanceCoordinator, kind: str) -> list[dict]:
    return [json.loads(e.payload_json) for e in s.events if e.event_type == kind]


def exit_sample(frame: int):
    return observation(1400, lateral=0, road=33, lane=2, frame=frame)


def through_sample(frame: int):
    return replace(
        observation(1250, road=1184, lane=-4, frame=frame),
        route_reference_distance_m=10,
        route_reference_half_width_m=1.75,
    )


@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("timed", [False, True])
def test_unpresented_pass_preserves_evidence_and_next_opportunity(draft, timed):
    s = subject()
    if timed:
        s.config = replace(s.config, eligibility_open_s=0, eligibility_close_s=20)
        s.start_active_drive(stamp(0))
    # DRAFT without mark_presented models HUD OFF / no successful display flip.
    s.observe(observation(250 if draft else 1100))
    s.observe(exit_sample(2))
    assert s.status is ExitAssistanceStatus.WAITING
    assert not payloads(s, "lc_completed")
    assert not payloads(s, "lc_maneuver_start")
    assert not payloads(s, "lc_boundary_crossing")
    physical = payloads(s, "lc_unpresented_path_observed")
    assert physical[-1]["observed_road_id"] == 33
    assert physical[-1]["outcome"] is None
    assert physical[-1]["t0_host_monotonic_ns"] is None
    s.observe(observation(250, frame=3))
    assert s.status is ExitAssistanceStatus.DRAFT
    s.mark_presented(s.prepare_presentation(MANUAL), MANUAL, stamp(35))
    s.observe(exit_sample(4))
    assert payloads(s, "lc_completed")[-1]["outcome"] == "EXIT"


@pytest.mark.parametrize("result", ["EXIT", "MISSED"])
def test_completed_result_is_immutable_while_observations_continue(result):
    s = subject()
    present(s)
    s.observe(exit_sample(2) if result == "EXIT" else through_sample(2))
    entry_count = len(payloads(s, "lc_exit_entry_observed"))
    for frame in range(3, 9):
        s.observe(exit_sample(frame) if frame % 2 else through_sample(frame))
    s.finish(ExitTerminationReason.ERROR, stamp(100))
    assert s.status is ExitAssistanceStatus.COMPLETED
    assert len(payloads(s, "lc_exit_entry_observed")) == entry_count
    assert [p["outcome"] for p in payloads(s, "lc_completed")] == [result]
    assert s._path_outcome == result
    assert s._latest_observation.carla_frame == 8
    other = subject()
    other.config = replace(other.config, lc_event_id="module-2-exit")
    present(other)
    other.observe(exit_sample(2))
    assert payloads(other, "lc_completed")[-1]["lc_event_id"] == "module-2-exit"


def geometry(source, frame, y=0.0, target=True, lane=-3):
    source.last_snapshot_sample = replace(
        snapshot(frame, 250 + frame, lane),
        timestamp=stamp(frame * 10),
        transform=carla.Transform(carla.Location(x=250 + frame, y=y)),
        lane_transform=carla.Transform(
            carla.Location(x=250 + frame, y=0 if lane == -3 else 3.5)
        ),
        right_lane_id=-4 if target else None,
        right_lane_transform=(
            carla.Transform(carla.Location(x=250 + frame, y=3.5)) if target else None
        ),
        right_lane_width_m=3.5 if target else None,
    )


@pytest.mark.parametrize("initial_target", [False, True])
def test_target_appears_after_t0_then_real_motion_and_crossing(initial_target):
    s = subject()
    source = SnapshotSource()
    observer = ResearchExitObservationSource(s.config.route, source)
    geometry(source, 1, target=initial_target)
    missing = observer.observe()
    if initial_target:
        assert missing.lateral_offset_m == pytest.approx(-3.5)
        assert missing.target_boundary_valid
    else:
        assert missing.lateral_offset_m is None
        assert not missing.target_boundary_valid
    present(s, missing)
    for frame, y in ((2, 0), (3, 0), (4, 1), (5, 2)):
        geometry(source, frame, y, lane=-4 if frame == 5 else -3)
        s.observe(observer.observe())
        if frame <= 3:
            assert not payloads(s, "lc_maneuver_start")
    assert len(payloads(s, "lc_maneuver_start")) == 1
    assert len(payloads(s, "lc_boundary_crossing")) == 1


@pytest.mark.parametrize("change", ["missing", "invalid", "road", "section"])
def test_reference_discontinuity_does_not_invent_movement_or_crossing(change):
    s = subject()
    present(s)
    changed = observation(260, lateral=-1.5, frame=2)
    if change == "missing":
        changed = replace(changed, lateral_offset_m=None)
    elif change == "invalid":
        changed = replace(changed, target_boundary_valid=False)
    elif change == "road":
        changed = replace(changed, road_id=40)
    else:
        changed = replace(changed, section_id=1)
    s.observe(changed)
    s.observe(
        replace(
            changed,
            lateral_offset_m=-1.5,
            target_boundary_valid=True,
            timestamp=stamp(30),
            carla_frame=3,
        )
    )
    assert not payloads(s, "lc_maneuver_start")
    assert not payloads(s, "lc_boundary_crossing")
    s.observe(
        replace(exit_sample(4), lateral_offset_m=None, target_boundary_valid=False)
    )
    assert payloads(s, "lc_completed")[-1]["outcome"] == "EXIT"


def test_target_loss_is_unknown_and_does_not_replace_target_with_current_lane():
    source = SnapshotSource()
    observer = ResearchExitObservationSource(subject().config.route, source)
    geometry(source, 1)
    assert observer.observe().lateral_offset_m == pytest.approx(-3.5)
    geometry(source, 2, target=False)
    missing = observer.observe()
    assert missing.lateral_offset_m is None
    assert not missing.target_boundary_valid


@pytest.mark.parametrize("physical", [exit_sample, through_sample])
def test_before_time_window_retains_physical_observation_without_result(physical):
    s = subject()
    s.config = replace(s.config, eligibility_open_s=1, eligibility_close_s=20)
    s.start_active_drive(stamp(0))
    s.observe(physical(1))
    assert s.status is ExitAssistanceStatus.WAITING
    assert payloads(s, "lc_unpresented_path_observed")[-1]["outcome"] is None
    s.observe(replace(observation(250, frame=2), timestamp=stamp(1_000_000_000)))
    assert s.status is ExitAssistanceStatus.DRAFT


@pytest.mark.parametrize(
    "reason", [ExitTerminationReason.USER_EXIT, ExitTerminationReason.ERROR]
)
def test_unpresented_shutdown_does_not_turn_later_path_into_completion(reason):
    s = subject()
    s.observe(observation(1100))
    s.finish(reason, stamp(15))
    s.observe(exit_sample(2))
    assert s.status is ExitAssistanceStatus.INTERRUPTED
    assert not payloads(s, "lc_completed")
    assert payloads(s, "lc_finished")[-1]["termination_reason"] == reason.value


@pytest.mark.parametrize("result", [None, "EXIT", "MISSED"])
def test_added_evidence_roundtrips_existing_ledger_and_operator_reader(
    tmp_path, result
):
    s = subject()
    s.config = replace(
        s.config, lc_event_id=f"run:MODULE_1_RUNNING:{s.config.route.route_id}"
    )
    s.observe(exit_sample(1))
    if result is not None:
        present(s, observation(250, frame=2))
        s.observe(observation(260, lateral=-3.0, frame=3))
        s.observe(observation(270, lateral=-1.5, lane=-4, frame=4))
        s.observe(exit_sample(5) if result == "EXIT" else through_sample(5))
        s.observe(through_sample(6))
        s.observe(exit_sample(7))
    path = tmp_path / "events.csv"
    manifest = {
        "lc_event_id": s.config.lc_event_id,
        "route_id": s.config.route.route_id,
        "route_version": s.config.route.route_version,
        "context": "MODULE",
        "module": "MODULE_1",
    }
    _write_events(
        path,
        (
            ("p5_exit_manifest", 0, manifest),
            *(
                (
                    e.event_type,
                    e.timestamp.host.monotonic_ns,
                    json.loads(e.payload_json),
                )
                for e in s.events
            ),
        ),
    )
    (summary,) = reconstruct_exit_ledger(path)
    assert summary.outcome == result
    config = ResearchExitAssistanceConfig(
        tmp_path / "manifest.json", s.config.route.route_id, s.config.lc_event_id
    )
    recorded = configured_exit_result(path, config, live=_live(path, s.status.value))
    assert (
        recorded.classification.value
        == {
            None: "CONFIGURED_NOT_PRESENTED",
            "EXIT": "EXIT_CONFIRMED",
            "MISSED": "NORMAL_MISSED",
        }[result]
    )
    if result is not None:
        onset = payloads(s, "lc_maneuver_start")[-1]
        assert onset["target_lateral_offset_m"] == -3.0
        assert onset["target_boundary_valid"] is True
        assert onset["target_lane_width_m"] == 3.5
