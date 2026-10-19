"""Driving incidents must not close M2 or silently disappear from results."""

from dataclasses import replace

import pytest

from src.experiment.automation_interaction import DriverInput
from src.p7.types import P7Error
from tests.m2_hazard_fakes import Waypoint
from tests.test_m2_collision_diagnostics import collision
from tests.test_m2_hazard_v1 import prepare, scene_fixture, tick


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
def test_departure_recovery_reentry_and_window_expiry_continue_to_end(tmp_path, mode):
    scene, world, clock, events, records, _ = scene_fixture(tmp_path, mode=mode)
    # Keep the projected lane fixed while ego moves outside its width.
    world.map.get_waypoint = lambda location, **kwargs: Waypoint(location.x)
    tick(scene, world, 23.203, y=-2.813)
    tick(scene, world, 1, y=-3)
    assert scene.state == "SCHEDULED"
    departures = [p for n, p in events if n == "m2_hazard_driving_event"]
    assert len(departures) == 1
    assert departures[0]["lateral_m"] == pytest.approx(-2.813)
    assert departures[0]["threshold_m"] == pytest.approx(2.65)
    assert departures[0]["observation_context"]["frame"] is not None
    tick(scene, world, 1, y=0)
    assert any(n == "m2_hazard_road_departure_proxy_cleared" for n, _ in events)
    tick(scene, world, scene.next_attempt - clock(), y=-3)
    assert scene.backend.lead is None
    assert scene.last_reason == "ROAD_DEPARTURE_PROXY_ACTIVE"
    tick(scene, world, 601 - clock(), y=-3)
    assert scene.state == "NOT_OCCURRED"
    tick(scene, world, 780 - clock(), y=0)
    scene.finalize("M2_END")
    assert records[0]["driving_event_counts"]["ROAD_DEPARTURE_PROXY"] == 2
    assert records[0]["execution_status"] == "DEVELOPMENT_RUN_ENDED"
    assert not records[0]["partial_data"]
    assert not records[0]["road_departure_proxy_active_at_end"]
    assert records[0]["raw_storage"]["pending_sample_count"] == 0


def test_missing_driving_waypoint_is_recorded_without_aborting(tmp_path):
    scene, world, _, events, _, _ = scene_fixture(tmp_path)
    tick(scene, world, 1)
    source = scene.session.persistence.source
    source.last_snapshot_sample = replace(
        source.last_snapshot_sample, lane_transform=None, lane_width_m=None
    )
    scene.after_control(DriverInput(), 0, 0)
    event = next(p for n, p in events if n == "m2_hazard_driving_event")
    assert event["method"] == "NO_DRIVING_WAYPOINT"
    assert scene.state == "SCHEDULED"
    scene.finalize("USER_EXIT")


@pytest.mark.parametrize("mode", ["MANUAL", "NOA_ACTIVE"])
def test_collision_after_preparation_preserves_scene_and_raw_observation(tmp_path, mode):
    scene, world, _, events, records, _ = scene_fixture(tmp_path, mode=mode)
    prepare(scene, world)
    scene.backend._collision(collision(world))
    tick(scene, world, 0.1)
    assert scene.state == "OCCLUDED"
    tick(scene, world, 0.1)
    scene.finalize("USER_EXIT")
    assert records[0]["driving_event_counts"]["COLLISION"] == 1
    assert records[0]["error"] is None
    assert any(n == "m2_hazard_raw_samples" for n, _ in events)


def test_technical_failure_still_propagates(tmp_path, monkeypatch):
    scene, world, _, _, _, _ = scene_fixture(tmp_path)
    monkeypatch.setattr(scene.backend, "failures", lambda _: {"reason": "ACTOR_LOST"})
    with pytest.raises(P7Error, match="ACTOR_LOST") as caught:
        tick(scene, world, 1)
    scene.finalize("ERROR", caught.value)
