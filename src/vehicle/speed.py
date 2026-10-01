from __future__ import annotations

from math import sqrt


def calculate_speed_kmh(x: float, y: float, z: float) -> float:
    """Convert a CARLA velocity vector in metres per second to km/h."""
    return sqrt(x**2 + y**2 + z**2) * 3.6
