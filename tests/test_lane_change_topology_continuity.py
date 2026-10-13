from __future__ import annotations

import carla
import pytest

from src.experiment.lane_change import LaneChangeDirection
from src.vehicle.carla_lane_topology import capture_lane_pair_topology
from tests.lane_change_topology_fakes import (
    TopologyScenario,
    topology_rig,
    topology_scenario,
)
from tests.test_lane_change_execution_lifecycle import Rig, sample


def observe_connected_completion(
    rig: Rig,
    scenario: TopologyScenario,
    direction: LaneChangeDirection,
) -> None:
    sign = -1.0 if direction is LaneChangeDirection.LEFT else 1.0
    target_lane = -2 if direction is LaneChangeDirection.LEFT else -4
    scenario.carla_map.current = scenario.new_source
    rig.persistence.source.last_snapshot_sample = sample(
        11,
        x=11.0,
        y=1.8 * sign,
        road_id=scenario.new_source.road_id,
        section_id=scenario.new_source.section_id,
        lane_id=-3,
        lane_transform=scenario.new_source.transform,
        left_lane_id=target_lane if direction is LaneChangeDirection.LEFT else None,
        left_transform=(
            scenario.new_target.transform
            if direction is LaneChangeDirection.LEFT
            else None
        ),
        left_road_id=scenario.new_target.road_id,
        left_section_id=scenario.new_target.section_id,
        right_lane_id=target_lane if direction is LaneChangeDirection.RIGHT else None,
        right_transform=(
            scenario.new_target.transform
            if direction is LaneChangeDirection.RIGHT
            else None
        ),
        right_road_id=scenario.new_target.road_id,
        right_section_id=scenario.new_target.section_id,
    )
    rig.observer.after_control_applied()
    scenario.carla_map.current = scenario.new_target
    rig.persistence.source.last_snapshot_sample = sample(
        12,
        x=12.0,
        y=3.5 * sign,
        road_id=scenario.new_target.road_id,
        section_id=scenario.new_target.section_id,
        lane_id=target_lane,
        lane_transform=scenario.new_target.transform,
        left_lane_id=-3 if direction is LaneChangeDirection.RIGHT else None,
        left_transform=(
            scenario.new_source.transform
            if direction is LaneChangeDirection.RIGHT
            else None
        ),
        left_road_id=scenario.new_source.road_id,
        left_section_id=scenario.new_source.section_id,
        right_lane_id=-3 if direction is LaneChangeDirection.LEFT else None,
        right_transform=(
            scenario.new_source.transform
            if direction is LaneChangeDirection.LEFT
            else None
        ),
        right_road_id=scenario.new_source.road_id,
        right_section_id=scenario.new_source.section_id,
    )
    rig.observer.after_control_applied()


@pytest.mark.parametrize("direction", list(LaneChangeDirection))
@pytest.mark.parametrize(
    "segments",
    [
        ((50, 0), (42, 0)),
        ((38, 0), (1091, 0)),
        ((36, 0), (36, 1)),
        ((36, 0), (37, 0), (38, 0)),
    ],
    ids=[
        "synthetic-log-identities-50-to-42",
        "synthetic-log-identities-38-to-1091",
        "synthetic-section-transition",
        "synthetic-multi-edge-transition",
    ],
)
def test_connected_lane_pair_completes_across_topology_transition(
    direction: LaneChangeDirection,
    segments: tuple[tuple[int, int], ...],
) -> None:
    scenario = topology_scenario(direction, segments)
    rig = topology_rig(direction, scenario)

    observe_connected_completion(rig, scenario, direction)

    lifecycles = [
        payload["lifecycle"]
        for event, payload in rig.persistence.events
        if event == "lane_change_lifecycle"
    ]
    selected = (
        carla.VehicleLightState.LeftBlinker
        if direction is LaneChangeDirection.LEFT
        else carla.VehicleLightState.RightBlinker
    )
    assert lifecycles == ["REQUESTED", "SUBMITTED_TO_TM", "IN_PROGRESS", "COMPLETED"]
    assert rig.backend.lane_change_in_flight is None
    assert not rig.vehicle.get_light_state() & selected


def test_observation_queries_topology_once_before_geometry() -> None:
    direction = LaneChangeDirection.LEFT
    scenario = topology_scenario(direction)
    rig = topology_rig(direction, scenario)
    scenario.carla_map.current = scenario.new_source
    before = scenario.carla_map.lookup_count
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

    assert scenario.carla_map.lookup_count - before == 1


def test_terminal_topology_self_pairs_do_not_create_false_merge() -> None:
    direction = LaneChangeDirection.LEFT
    scenario = topology_scenario(direction)
    scenario.carla_map.topology.extend(
        [
            (scenario.new_source, scenario.new_source),
            (scenario.new_target, scenario.new_target),
        ]
    )
    topology = capture_lane_pair_topology(
        scenario.carla_map,
        scenario.old_source.transform,
        direction,
    )
    assert topology is not None
    scenario.carla_map.current = scenario.new_source

    identities = topology.advance(scenario.new_source.transform)

    assert identities == (scenario.new_source.identity, scenario.new_target.identity)
