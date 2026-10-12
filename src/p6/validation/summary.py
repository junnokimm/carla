from __future__ import annotations

from collections import Counter
from statistics import median

from src.p6.validation.observation import Observation

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]


class TargetStats:
    def __init__(self) -> None:
        self.total = self.any_valid = self.both_valid = self.classifiable = self.hits = 0
        self.confusion: Counter[str] = Counter()
        self.xs: list[float] = []
        self.ys: list[float] = []

    def add(self, target: str, observation: Observation) -> None:
        self.total += 1
        self.any_valid += observation.any_eye_valid
        self.both_valid += observation.both_eye_valid
        self.classifiable += observation.candidate in (
            "FORWARD", "REAR_MIRROR", "LEFT_MIRROR", "RIGHT_MIRROR"
        )
        self.hits += (not observation.any_eye_valid if target == "MISSING"
                      else observation.candidate == target)
        self.confusion[observation.candidate] += 1
        if observation.normalized is not None:
            self.xs.append(observation.normalized.x)
            self.ys.append(observation.normalized.y)

    def result(self) -> dict[str, Json]:
        return {
            "total_samples": self.total,
            "any_eye_valid_count": self.any_valid,
            "any_eye_valid_ratio": self.any_valid / self.total if self.total else None,
            "both_eye_valid_count": self.both_valid,
            "both_eye_valid_ratio": self.both_valid / self.total if self.total else None,
            "classifiable_sample_count": self.classifiable,
            "expected_aoi_hit_count": self.hits,
            "expected_aoi_hit_ratio": self.hits / self.total if self.total else None,
            "confusion_counts": dict(self.confusion),
            "invalid_missing_count": self.total - self.classifiable,
            "no_valid_eye_count": self.total - self.any_valid,
            "median_normalized_x": median(self.xs) if self.xs else None,
            "median_normalized_y": median(self.ys) if self.ys else None,
        }
