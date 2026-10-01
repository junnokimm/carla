from __future__ import annotations

import pytest

from src.experiment.lane_change import (
    AdjacentLaneObservation,
    LaneChangeDirection,
    LaneChangeRecommendation,
    LaneChangeRequirement,
    recommend_lane_change,
)


@pytest.mark.parametrize(
    "observation",
    [
        AdjacentLaneObservation(-3, None, None),
        AdjacentLaneObservation(-3, -2, -4),
    ],
)
def test_keep_lane_never_recommends_lane_change(
    observation: AdjacentLaneObservation,
) -> None:
    assert recommend_lane_change(LaneChangeRequirement.KEEP_LANE, observation) is None


def test_left_requirement_recommends_observed_left_lane() -> None:
    observation = AdjacentLaneObservation(-3, -2, -4)

    result = recommend_lane_change(LaneChangeRequirement.LEFT, observation)

    assert result == LaneChangeRecommendation(LaneChangeDirection.LEFT, -2)


def test_right_requirement_recommends_observed_right_lane() -> None:
    observation = AdjacentLaneObservation(-3, -2, -4)

    result = recommend_lane_change(LaneChangeRequirement.RIGHT, observation)

    assert result == LaneChangeRecommendation(LaneChangeDirection.RIGHT, -4)


def test_left_requirement_has_no_opposite_direction_fallback() -> None:
    observation = AdjacentLaneObservation(-3, None, -4)

    result = recommend_lane_change(LaneChangeRequirement.LEFT, observation)

    assert result is None


def test_right_requirement_has_no_opposite_direction_fallback() -> None:
    observation = AdjacentLaneObservation(-3, -2, None)

    result = recommend_lane_change(LaneChangeRequirement.RIGHT, observation)

    assert result is None


def test_recommendation_is_deterministic_without_interpreting_lane_id_sign() -> None:
    observation = AdjacentLaneObservation(-3, 2, -4)

    first = recommend_lane_change(LaneChangeRequirement.LEFT, observation)
    second = recommend_lane_change(LaneChangeRequirement.LEFT, observation)

    expected = LaneChangeRecommendation(LaneChangeDirection.LEFT, 2)
    assert first == expected
    assert second == expected
