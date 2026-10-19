"""Conservative empirical tablet AOI in the extended Tobii display plane.

Coordinates outside [0, 1] can be SDK-valid. They are NOT physical tablet
coordinates. The profile covers observed gaze directions, not an inferred
four-corner tablet rectangle, and must be checked after personal calibration.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from src.p6.models import Validity


def tracked_eyes(sample):
    return {name: (eye.x, eye.y) for name, eye in
            (("left", sample.left), ("right", sample.right))
            if (eye.validity is Validity.VALID or eye.invalid_reason == "OUTSIDE_DISPLAY_AREA")
            and eye.x is not None and eye.y is not None
            and math.isfinite(eye.x) and math.isfinite(eye.y)}


def inside_convex(point, polygon):
    signs = []
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        if abs(cross) > 1e-10:
            signs.append(cross > 0)
    return not signs or all(sign == signs[0] for sign in signs)


def convex_hull(points):
    points = sorted(set(map(tuple, points)))
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lower, upper = [], []
    for chain, sequence in ((lower, points), (upper, reversed(points))):
        for point in sequence:
            while len(chain) >= 2 and cross(chain[-2], chain[-1], point) <= 0:
                chain.pop()
            chain.append(point)
    return lower[:-1] + upper[:-1]


class ExternalTablet:
    def __init__(self, profile):
        self.profile = profile
        if profile.get("schema_version") != 1 or profile.get("method") != "PER_EYE_OBSERVED_CONVEX_HULL":
            raise ValueError("UNSUPPORTED_TABLET_PROFILE")
        if not isinstance(profile.get("serial_number"), str) or not profile["serial_number"]:
            raise ValueError("TABLET_PROFILE_REQUIRES_DEVICE")
        if profile.get("status") != "PROVISIONAL_PARTIAL_EMPIRICAL_MAPPING":
            raise ValueError("INVALID_TABLET_PROFILE_STATUS")
        self.polygons = profile["polygons"]
        for name in ("left", "right"):
            points = self.polygons[name]
            if len(points) < 3 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
                raise ValueError("INVALID_TABLET_POLYGON")
            if set(map(tuple, convex_hull(points))) != set(map(tuple, points)):
                raise ValueError("TABLET_POLYGON_MUST_BE_CONVEX")
            # Normalize vertex order; a shuffled polygon must not silently misclassify.
            self.polygons[name] = convex_hull(points)
        for key in ("top_left", "top_right", "bottom_left", "bottom_right"):
            point = profile["display_area"][key]
            if len(point) != 3 or not all(math.isfinite(v) for v in point):
                raise ValueError("INVALID_TABLET_DISPLAY_BINDING")

    @classmethod
    def load(cls, path):
        raw = Path(path).read_bytes()
        item = cls(json.loads(raw.decode("utf-8-sig")))
        item.sha256 = hashlib.sha256(raw).hexdigest()
        return item

    def display_matches(self, record):
        try:
            return (record["status"] == "OK" and record["serial_number"] == self.profile["serial_number"]
                    and all(math.isfinite(actual) and abs(actual - expected) <= 1.0
                            for corner in ("top_left", "top_right", "bottom_left", "bottom_right")
                            for actual, expected in zip(record["display_area"][corner],
                                                        self.profile["display_area"][corner], strict=True)))
        except (KeyError, TypeError, ValueError):
            return False

    def classify(self, sample):
        if sample.source_metadata.get("serial_number") != self.profile["serial_number"]:
            return False, "TABLET_DEVICE_MISMATCH", {}
        hits = {eye: inside_convex(point, self.polygons[eye]) for eye, point in tracked_eyes(sample).items()}
        if hits and all(hits.values()):
            return True, "EMPIRICAL_TABLET_MONOCULAR" if len(hits) == 1 else "EMPIRICAL_TABLET_BINOCULAR", hits
        if any(hits.values()):
            return False, "TABLET_EYE_DISAGREEMENT", hits
        return False, "OUTSIDE_OBSERVED_TABLET_REGION", hits
