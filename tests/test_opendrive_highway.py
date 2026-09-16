from __future__ import annotations


class FakeWaypoint:
    def __init__(
        self,
        road_id: int,
        lane_id: int,
        successor_road_id: int,
        predecessor_road_id: int,
    ) -> None:
        self.road_id = road_id
        self.lane_id = lane_id
        self._successor_road_id = successor_road_id
        self._predecessor_road_id = predecessor_road_id

    def next(self, distance: float) -> list[FakeWaypoint]:
        assert distance == 2.0
        return [FakeWaypoint(self._successor_road_id, self.lane_id, 0, 0)]

    def previous(self, distance: float) -> list[FakeWaypoint]:
        assert distance == 2.0
        return [FakeWaypoint(self._predecessor_road_id, self.lane_id, 0, 0)]


class FakeMap:
    def get_waypoint_xodr(self, road_id: int, lane_id: int, s: float) -> FakeWaypoint:
        assert lane_id in {-4, -3, -2, 2, 3, 4}
        assert s > 0.0
        return FakeWaypoint(
            road_id,
            lane_id,
            (road_id - 2) % 4 + 1 if lane_id > 0 else road_id % 4 + 1,
            road_id if lane_id > 0 else (road_id - 2) % 4 + 1,
        )


def test_validate_road_network_reports_closed_bidirectional_cycle() -> None:
    from src.scenario.opendrive_highway import validate_road_network

    report = validate_road_network(FakeMap())

    assert report.road_ids == (1, 2, 3, 4)
    assert report.lane_ids == (-4, -3, -2, 2, 3, 4)
    assert report.checked_connections == 24
