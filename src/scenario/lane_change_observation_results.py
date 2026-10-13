from __future__ import annotations

from src.scenario.lane_change_geometry import (
    LaneChangeObservationResult,
    LaneChangeObservationStatus,
)


class LaneChangeObservationResultFactory:
    unverified_reason: str | None
    target_entered: bool
    boundary_outside_frame: int | None
    boundary_inside_frame: int | None
    last_frame: int | None

    def _unverified(
        self,
        reason: str,
        frame: int | None,
        previous: int | None = None,
    ) -> LaneChangeObservationResult:
        if self.unverified_reason is None:
            self.unverified_reason = reason
        return LaneChangeObservationResult(
            LaneChangeObservationStatus.UNVERIFIED,
            self.unverified_reason,
            previous,
            frame,
            None if frame is None or previous is None else frame - previous,
            None,
            None,
            self.target_entered,
            None,
            None,
            None,
            None,
            None,
            self.boundary_outside_frame,
            self.boundary_inside_frame,
        )

    def _result(
        self,
        status: LaneChangeObservationStatus,
        reason: str,
        frame: int | None = None,
    ) -> LaneChangeObservationResult:
        return LaneChangeObservationResult(
            status,
            reason,
            self.last_frame,
            frame,
            0 if frame is not None else None,
            None,
            None,
            self.target_entered,
            None,
            None,
            None,
            None,
            None,
            self.boundary_outside_frame,
            self.boundary_inside_frame,
        )
