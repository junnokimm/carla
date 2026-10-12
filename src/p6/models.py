from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None


class SourceStatus(StrEnum):
    READY = "READY"
    DISCONNECTED = "DISCONNECTED"
    SDK_ABSENT = "SDK_ABSENT"
    ADAPTER_UNCONFIRMED = "BLOCKED_MODEL_SDK_UNCONFIRMED"
    CALIBRATION_FAILED = "CALIBRATION_FAILED"


class ClockKind(StrEnum):
    DEVICE = "DEVICE"
    SYSTEM = "SYSTEM"


class Validity(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class EyeDatum:
    x: float | None
    y: float | None
    validity: Validity
    invalid_reason: str | None

    def __post_init__(self) -> None:
        if self.validity is Validity.INVALID and self.invalid_reason is None:
            object.__setattr__(self, "invalid_reason", "UNSPECIFIED")


@dataclass(frozen=True, slots=True)
class RawGazeSample:
    source_sequence: int
    source_timestamp: int | float
    source_unit: str
    clock_kind: ClockKind
    left: EyeDatum
    right: EyeDatum
    source_metadata: dict[str, JsonScalar]


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    status: str
    aoi_validation_passed: bool
    details: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    identifier: str
    model: str
    connected: bool
