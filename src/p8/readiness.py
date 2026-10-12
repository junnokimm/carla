import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.p8.model import P8Error


@dataclass(frozen=True, slots=True)
class Readiness:
    eye_tracker_required: bool = True
    eye_tracker_available: bool = False
    eye_tracker_calibrated: bool = False
    screen_aoi_ready: bool = False
    tablet_ready: bool = False
    tablet_aoi_ready: bool = False
    clock_sync_ready: bool = False
    p7_live_validated: bool = False
    p1_p5_matrix_ready: bool = False
    rescue_owner_ready: bool = False
    final_parameters_ready: bool = False
    final_video_ready: bool = False

    def blockers(self) -> tuple[str, ...]:
        return tuple(name for name, ready in asdict(self).items() if not ready)


def require_participant_ready(readiness: Readiness, *, source_kind: str) -> None:
    blockers = list(readiness.blockers())
    if source_kind != "REAL":
        blockers.append("DEV_STUB_OR_SYNTHETIC_FORBIDDEN_IN_PARTICIPANT_MODE")
    blockers.append("PARTICIPANT_RUNTIME_NOT_IMPLEMENTED")
    raise P8Error("PARTICIPANT_BLOCKED:" + ",".join(blockers))


def load_readiness(path: Path) -> tuple[Readiness, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if set(raw) != {"schema_version", "required", "reported_history"} or raw["schema_version"] != 1:
            raise P8Error("INVALID_READINESS_SCHEMA")
        flags = raw["required"]
        if set(flags) != set(asdict(Readiness())) or any(type(value) is not bool for value in flags.values()):
            raise P8Error("READINESS_REQUIRES_EXPLICIT_BOOLEAN_FIELDS")
        history = raw["reported_history"]
        if not isinstance(history, dict) or any(not isinstance(value, str) for value in history.values()):
            raise P8Error("INVALID_READINESS_HISTORY")
        return Readiness(**flags), history
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise P8Error(f"READINESS_LOAD_FAILED:{error}") from error
