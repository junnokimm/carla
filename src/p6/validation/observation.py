from __future__ import annotations

from dataclasses import dataclass

from src.p6.aoi import AoiLayout, Point
from src.p6.models import RawGazeSample, Validity
from src.p6.validation.mapping import WindowGeometry, candidate_aoi, to_render_point


@dataclass(frozen=True, slots=True)
class Observation:
    any_eye_valid: bool
    both_eye_valid: bool
    classification_input_valid: bool
    normalized: Point | None
    client: Point | None
    candidate: str
    missing_reason: str | None
    production_aoi: str


def observe(sample: RawGazeSample, layout: AoiLayout, *, geometry: WindowGeometry | None,
            operator_confirmed: bool) -> Observation:
    left_valid = sample.source_metadata.get("left_validity") == 1
    right_valid = sample.source_metadata.get("right_validity") == 1
    eyes = [eye for eye in (sample.left, sample.right)
            if eye.validity is Validity.VALID and eye.x is not None and eye.y is not None]
    normalized = None
    if eyes:
        normalized = Point(sum(eye.x for eye in eyes if eye.x is not None) / len(eyes),
                           sum(eye.y for eye in eyes if eye.y is not None) / len(eyes))
    client, reason, candidate = None, None, "INVALID"
    if normalized is None:
        reason = ";".join(sorted({eye.invalid_reason or "INVALID"
                                  for eye in (sample.left, sample.right)}))
    elif not operator_confirmed:
        reason = "ETM_DISPLAY_UNCONFIRMED"
    elif geometry is None:
        reason = "MAPPING_UNAVAILABLE"
    else:
        client, reason = to_render_point(normalized, geometry, logical_size=layout.mapping.render_size)
        if client is not None:
            candidate = candidate_aoi(client, layout)
            if candidate in ("NO_AOI", "INVALID_LAYOUT"):
                reason = candidate
    production = ("NO_AOI" if normalized is None else
                  layout.classify(normalized.x, normalized.y).value)
    return Observation(left_valid or right_valid, left_valid and right_valid, bool(eyes),
                       normalized, client, candidate, reason, production)
