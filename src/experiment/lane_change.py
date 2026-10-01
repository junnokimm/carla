from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never


class LaneChangeRequirement(StrEnum):
    """Explicit lane requirement supplied by a future route or scenario layer."""

    KEEP_LANE = "KEEP_LANE"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class LaneChangeDirection(StrEnum):
    """Direction of a recommended lane change."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"


@dataclass(frozen=True, slots=True)
class AdjacentLaneObservation:
    """Identify the current lane and immediate available driving lanes."""

    current_lane_id: int
    left_lane_id: int | None
    right_lane_id: int | None


@dataclass(frozen=True, slots=True)
class LaneChangeRecommendation:
    """Describe a recommended direction and its observed target lane."""

    direction: LaneChangeDirection
    target_lane_id: int


def recommend_lane_change(
    requirement: LaneChangeRequirement,
    observation: AdjacentLaneObservation,
) -> LaneChangeRecommendation | None:
    """Recommend only the explicitly required immediate adjacent lane."""
    match requirement:
        case LaneChangeRequirement.KEEP_LANE:
            return None
        case LaneChangeRequirement.LEFT:
            if observation.left_lane_id is None:
                return None
            return LaneChangeRecommendation(
                LaneChangeDirection.LEFT,
                observation.left_lane_id,
            )
        case LaneChangeRequirement.RIGHT:
            if observation.right_lane_id is None:
                return None
            return LaneChangeRecommendation(
                LaneChangeDirection.RIGHT,
                observation.right_lane_id,
            )
        case unreachable:
            assert_never(unreachable)
