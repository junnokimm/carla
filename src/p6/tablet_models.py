from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True, slots=True)
class Circle:
    circle_id: str
    x: float
    y: float
    diameter_px: int
    target: bool


@dataclass(frozen=True, slots=True)
class StimulusConfig:
    circles: tuple[Circle, ...]
    contrast: float = 1.0
    timeout_seconds: float | None = None
    development_nonresponse_enabled: bool = False


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration_seconds: float
    muted: bool
    loop: bool
    playback_rate: float


@dataclass(frozen=True, slots=True)
class MediaSelection:
    filename: str
    version: str
    exists: bool
    sha256: str | None


@dataclass(frozen=True, slots=True)
class Trial:
    trial_uuid: str
    session_epoch: str
    stimulus: StimulusConfig | None


@dataclass(frozen=True, slots=True)
class TabletEvent:
    event_id: str
    event_type: str
    run_epoch: str
    trial_uuid: str | None
    receive_monotonic_ns: int
    receipt_session_seconds: float
    browser_performance_ms: float | None
    browser_time_origin_ms: float | None
    browser_mapping_seconds: float | None
    mapping_status: str
    disposition: str
    detail: str | None
    correct: bool | None


@dataclass(frozen=True, slots=True)
class EventRequest:
    event_type: str
    event_id: str
    run_epoch: str
    trial_uuid: str | None = None
    performance_ms: float | None = None
    time_origin_ms: float | None = None
    detail: str | None = None
    correct: bool | None = None
    disposition: str = "accepted"
    received_monotonic_ns: int | None = None


@dataclass(frozen=True, slots=True)
class TabletState:
    run_id: str
    run_epoch: str
    current_trial: Trial | None
    circles: tuple[Circle, ...]
    condition: str | None
    module: str | None
    stage: str
    media: MediaSelection | None
    paused: bool
    error: str | None


def tablet_state_json(state: TabletState) -> dict[str, JsonValue]:
    trial = (
        None
        if state.current_trial is None
        else {
            "trial_uuid": state.current_trial.trial_uuid,
            "session_epoch": state.current_trial.session_epoch,
        }
    )
    return {
        "run_id": state.run_id,
        "run_epoch": state.run_epoch,
        "current_trial": trial,
        "circles": [asdict(circle) for circle in state.circles],
        "stimulus": None
        if state.current_trial is None or state.current_trial.stimulus is None
        else asdict(state.current_trial.stimulus),
        "condition": state.condition,
        "module": state.module,
        "stage": state.stage,
        "media": None if state.media is None else asdict(state.media),
        "paused": state.paused,
        "error": state.error,
    }
