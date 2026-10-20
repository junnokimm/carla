import carla
import pytest

from src.p7.types import P7Error
from tests.m2_hazard_fakes import Actor
from tests.test_clear_road_hazard import expose, setup
from tests.test_m2_hazard_v1 import tick


def test_target_and_speed_failures_are_distinct_from_unchecked_conditions(tmp_path):
    scene, world, _, _, records, _ = setup(tmp_path)
    tick(scene, world, scene.planned - 6, speed=8 / 3.6)
    evidence = scene.last_readiness
    assert evidence["target_speed_kmh"] == 43.2
    assert evidence["ego_speed_kmh"] == 8
    assert evidence["target_source"] == "EXISTING_TM_ROAD_SPEED_LIMIT_ZERO_PERCENT_DIFFERENCE"
    assert evidence["checks"]["target_speed_available"]["status"] == "PASS"
    assert evidence["checks"]["speed_in_tolerance"]["status"] == "FAIL"
    assert evidence["checks"]["route_available"]["status"] == "NOT_CHECKED"
    assert evidence["checks"]["approach_clear"]["status"] == "NOT_CHECKED"
    world.vehicle.speed_limit_kmh = 0
    tick(scene, world, 1.1)
    assert scene.last_readiness["target_speed_kmh"] is None
    assert scene.last_readiness["checks"]["target_speed_available"]["reason"] == "TARGET_SPEED_UNAVAILABLE"
    assert scene.last_readiness["checks"]["speed_in_tolerance"]["status"] == "NOT_CHECKED"
    scene.finalize("M2_END")
    assert records[-1]["last_readiness"] == scene.last_readiness


def test_lead_vehicle_failure_has_actor_evidence(tmp_path):
    scene, world, _, _, _, _ = setup(tmp_path)
    tick(scene, world, scene.planned - 7)
    lead = Actor(999, pose=carla.Transform(carla.Location(x=world.vehicle.transform.location.x + 35)))
    world.actors.append(lead)
    tick(scene, world, 1.1)
    evidence = scene.last_readiness
    assert evidence["checks"]["speed_in_tolerance"]["status"] == "PASS"
    assert evidence["checks"]["approach_clear"]["reason"] == "APPROACH_CORRIDOR_OCCUPIED"
    assert evidence["blocking_actor_id"] == 999
    assert evidence["checks"]["camera_available"]["status"] == "NOT_CHECKED"
    assert scene.backend.motion_started is None and scene.scene_failure is None
    scene.finalize("M2_END")


def test_new_interlock_attempt_does_not_reuse_old_pass_or_make_extra_rpcs(tmp_path, monkeypatch):
    scene, world, _, _, _, _ = setup(tmp_path)
    tick(scene, world, scene.planned - 6)
    previous = scene.last_readiness
    assert previous["checks"]["approach_clear"]["status"] == "PASS"
    scene.session.bundle.traffic_manager_backend.lane_change_in_flight = "changing"
    def forbidden():
        raise AssertionError("No readiness RPC while the interlock is active")
    monkeypatch.setattr(scene.backend, "target_speed", forbidden)
    tick(scene, world, 1.1)
    current = scene.last_readiness
    assert current["checks"]["approach_clear"]["status"] == "NOT_CHECKED"
    assert current["target_speed_kmh"] is None
    assert current["checks"]["target_speed_available"]["reason"] == "P5_OR_GENERAL_LANE_CHANGE_IN_FLIGHT"
    assert previous["checks"]["approach_clear"]["status"] == "PASS"
    assert scene.scene_failure is None
    scene.finalize("M2_END")


@pytest.mark.parametrize("failure", [P7Error("ROUTE_UNAVAILABLE"), RuntimeError("RPC timeout")])
def test_readiness_failure_is_logged_and_never_stops_driving(tmp_path, monkeypatch, failure):
    from src.p7.moving_route import MovingRoute
    scene, world, clock, _, _, _ = setup(tmp_path)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(MovingRoute, "select", fail)
    tick(scene, world, scene.planned - 6)
    check = scene.last_readiness["checks"]["route_available"]
    assert check["status"] == ("FAIL" if isinstance(failure, P7Error) else "ERROR")
    assert scene.last_readiness["checks"]["approach_clear"]["status"] == "NOT_CHECKED"
    tick(scene, world, 780 - clock())
    scene.finalize("M2_END")
    assert world.vehicle.is_alive and world.vehicle.applied_controls == []


def test_diagnostic_log_write_failure_does_not_cancel_hazard_or_driving(tmp_path, monkeypatch):
    scene, world, _, _, records, _ = setup(tmp_path)
    original = scene.services.emit
    def fail_diagnostic(kind, payload):
        if kind in ("m2_hazard_readiness_evaluated", "m2_hazard_preparation_deferred"):
            raise OSError("diagnostic disk failure")
        original(kind, payload)
    monkeypatch.setattr(scene.services, "emit", fail_diagnostic)
    expose(scene, world)
    assert scene.scene_failure is None and scene.continuation_errors
    assert scene.last_readiness["reason"] == "READY_FOR_REVEAL"
    assert all(c["status"] == "PASS" for c in scene.last_readiness["checks"].values())
    scene.finalize("M2_END")
    assert records[-1]["nonfatal_recording_errors"]
    assert records[-1]["last_readiness"]["reason"] == "READY_FOR_REVEAL"
    assert world.vehicle.applied_controls == []
