from __future__ import annotations

import carla

from src.experiment.automation_interaction_types import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.lane_change import LaneChangeDirection
from src.scenario.lane_change_request import build_lane_change_tracker
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneAdapter
from src.vehicle.carla_lane_topology import capture_lane_pair_topology
from tests.lane_change_topology_fakes import (
    TopologyScenario,
    TopologyWaypoint,
    topology_rig,
    topology_scenario,
)
from tests.test_lane_change_execution_lifecycle import make_rig, sample
from tests.test_lane_change_topology_continuity import (
    observe_connected_completion,
)


def assert_transition_unverified(scenario: TopologyScenario) -> None:
    direction = LaneChangeDirection.LEFT
    rig = topology_rig(direction, scenario)

    observe_connected_completion(rig, scenario, direction)

    lifecycles = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert lifecycles[-1]["lifecycle"] == "UNVERIFIED"
    assert lifecycles[-1]["reason"] == "GEOMETRY_CONTINUITY_UNAVAILABLE"
    assert rig.backend.lane_change_in_flight is not None
    assert rig.vehicle.get_light_state() & carla.VehicleLightState.LeftBlinker


def test_intermediate_junction_fails_closed() -> None:
    scenario = topology_scenario(
        LaneChangeDirection.LEFT,
        ((36, 0), (37, 0), (38, 0)),
    )
    scenario.source_path[1].is_junction = True

    assert_transition_unverified(scenario)


def test_intermediate_dead_fork_fails_closed() -> None:
    scenario = topology_scenario(
        LaneChangeDirection.LEFT,
        ((36, 0), (37, 0), (38, 0)),
    )
    branch = TopologyWaypoint(
        (99, 0, -3),
        carla.Transform(carla.Location(x=12.0, y=8.0)),
    )
    scenario.carla_map.topology.append((scenario.source_path[1], branch))

    assert_transition_unverified(scenario)


def test_intermediate_merge_fails_closed() -> None:
    scenario = topology_scenario(
        LaneChangeDirection.LEFT,
        ((36, 0), (37, 0), (38, 0)),
    )
    incoming = TopologyWaypoint(
        (98, 0, -3),
        carla.Transform(carla.Location(x=11.0, y=8.0)),
    )
    scenario.carla_map.topology.append((incoming, scenario.source_path[1]))

    assert_transition_unverified(scenario)


def test_wrong_intermediate_lateral_adjacency_fails_closed() -> None:
    scenario = topology_scenario(
        LaneChangeDirection.LEFT,
        ((36, 0), (37, 0), (38, 0)),
    )
    scenario.source_path[1].left = TopologyWaypoint(
        (37, 0, -5),
        carla.Transform(carla.Location(x=11.0, y=-7.0)),
    )

    assert_transition_unverified(scenario)


def test_missing_intermediate_lateral_adjacency_fails_closed() -> None:
    scenario = topology_scenario(
        LaneChangeDirection.LEFT,
        ((36, 0), (37, 0), (38, 0)),
    )
    scenario.source_path[1].left = None

    assert_transition_unverified(scenario)


def test_disconnected_cycle_fails_closed() -> None:
    scenario = topology_scenario(
        LaneChangeDirection.LEFT,
        ((36, 0), (37, 0), (38, 0)),
    )
    old_source, middle_source, new_source = scenario.source_path
    old_target, middle_target, new_target = scenario.target_path
    scenario.carla_map.topology = [
        (old_source, middle_source),
        (middle_source, old_source),
        (old_target, middle_target),
        (middle_target, old_target),
        (new_source, new_source),
        (new_target, new_target),
    ]

    assert_transition_unverified(scenario)


def test_unavailable_waypoint_lookup_fails_closed() -> None:
    scenario = topology_scenario(LaneChangeDirection.LEFT)
    rig = topology_rig(LaneChangeDirection.LEFT, scenario)
    scenario.carla_map.current = None
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=11.0,
        y=-1.8,
        road_id=scenario.new_source.road_id,
        lane_id=-3,
        lane_transform=scenario.new_source.transform,
        left_lane_id=-2,
        left_transform=scenario.new_target.transform,
        left_road_id=scenario.new_target.road_id,
    )

    rig.observer.after_control_applied()

    lifecycle = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ][-1]
    assert lifecycle["lifecycle"] == "UNVERIFIED"
    assert lifecycle["reason"] == "GEOMETRY_CONTINUITY_UNAVAILABLE"


def test_identity_only_road_change_is_not_topology_proof() -> None:
    direction = LaneChangeDirection.RIGHT
    scenario = topology_scenario(direction)
    rig = topology_rig(direction, scenario)
    scenario.carla_map.current = scenario.old_source
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=11.0,
        y=1.8,
        road_id=scenario.new_source.road_id,
        lane_id=-3,
        lane_transform=scenario.new_source.transform,
        right_lane_id=-4,
        right_road_id=scenario.new_target.road_id,
        right_transform=scenario.new_target.transform,
    )

    rig.observer.after_control_applied()

    lifecycle = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ][-1]
    assert lifecycle["lifecycle"] == "UNVERIFIED"
    assert lifecycle["reason"] == "GEOMETRY_CONTINUITY_UNAVAILABLE"


def test_tracker_build_rejects_captured_pair_mismatch() -> None:
    direction = LaneChangeDirection.LEFT
    expected = topology_scenario(direction)
    mismatched = topology_scenario(direction, ((99, 0), (100, 0)))
    rig = make_rig(direction, submit_general=False)
    adapter = CarlaAdjacentLaneAdapter(expected.carla_map)
    eligibility = adapter.evaluate(rig.vehicle, direction)
    topology = capture_lane_pair_topology(
        mismatched.carla_map,
        mismatched.old_source.transform,
        direction,
    )
    assert topology is not None

    built = build_lane_change_tracker(
        "mismatch",
        direction,
        eligibility,
        rig.persistence.source.last_snapshot_sample,
        rig.backend.activation_generation,
        "GENERAL",
        None,
        rig.vehicle.bounding_box,
        AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
        topology,
    )

    assert built.tracker is None
    assert built.reason == "TOPOLOGY_BASELINE_IDENTITY_MISMATCH"


def test_submission_lookup_failure_is_recorded_immediately() -> None:
    direction = LaneChangeDirection.LEFT
    scenario = topology_scenario(direction)
    scenario.carla_map.lookup_queue = [
        scenario.old_source,
        scenario.old_source,
        None,
    ]
    rig = topology_rig(direction, scenario)

    lifecycles = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    assert [payload["lifecycle"] for payload in lifecycles] == [
        "REQUESTED",
        "SUBMITTED_TO_TM",
        "UNVERIFIED",
    ]
    assert lifecycles[-1]["reason"] == "SUBMISSION_TOPOLOGY_UNAVAILABLE"


def test_completion_releases_followup_request_after_connected_transition() -> None:
    direction = LaneChangeDirection.RIGHT
    scenario = topology_scenario(direction)
    rig = topology_rig(direction, scenario)
    observe_connected_completion(rig, scenario, direction)
    rig.vehicle.current_light_state |= carla.VehicleLightState.LeftBlinker

    rig.observer.update(DriverInput(lane_change_direction=LaneChangeDirection.LEFT))

    requests = [
        payload
        for event, payload in rig.persistence.events
        if event == "lane_change_request"
    ]
    forces = [
        value
        for name, value in rig.traffic_manager.calls
        if name == "force_lane_change"
    ]
    assert [request["disposition"] for request in requests] == [
        "REQUESTED",
        "REQUESTED",
    ]
    assert requests[0]["request_id"] != requests[1]["request_id"]
    assert forces == [True, False]
