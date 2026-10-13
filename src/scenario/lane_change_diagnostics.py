from __future__ import annotations

from math import degrees

from src.scenario.lane_change_geometry import LaneChangeObservationResult

DiagnosticValue = str | float | bool | int | None


def geometry_diagnostics(
    result: LaneChangeObservationResult | None,
) -> dict[str, DiagnosticValue]:
    if result is None:
        diagnostics: dict[str, DiagnosticValue] = {
            key: None
            for key in (
                "source_aligned",
                "target_aligned",
                "target_entered",
                "source_lateral_error_m",
                "source_heading_error_rad",
                "target_lateral_error_m",
                "target_heading_error_rad",
                "source_alignment_failure",
                "target_alignment_failure",
                "target_lane_center_x",
                "target_lane_center_y",
                "target_lane_heading_degrees",
                "target_lane_width_m",
                "center_tolerance_m",
                "heading_tolerance_rad",
                "boundary_outside_frame",
                "boundary_inside_frame",
            )
        }
        return diagnostics
    return {
        "source_aligned": result.source_aligned,
        "target_aligned": result.target_aligned,
        "target_entered": result.target_entered,
        "source_lateral_error_m": result.source_lateral_error_m,
        "source_heading_error_rad": result.source_heading_error_rad,
        "target_lateral_error_m": result.target_lateral_error_m,
        "target_heading_error_rad": result.target_heading_error_rad,
        "source_alignment_failure": result.source_alignment_failure,
        "target_alignment_failure": result.target_alignment_failure,
        "target_lane_center_x": result.target_lane_center_x,
        "target_lane_center_y": result.target_lane_center_y,
        "target_lane_heading_degrees": None
        if result.target_lane_heading_rad is None
        else degrees(result.target_lane_heading_rad),
        "target_lane_width_m": result.target_lane_width_m,
        "center_tolerance_m": result.center_tolerance_m,
        "heading_tolerance_rad": result.heading_tolerance_rad,
        "boundary_outside_frame": result.boundary_outside_frame,
        "boundary_inside_frame": result.boundary_inside_frame,
    }
