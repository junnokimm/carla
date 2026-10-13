from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
type SourceMode = Literal["synthetic", "real"]


@dataclass(frozen=True, slots=True)
class P6ConfigError(ValueError):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class TabletServerConfig:
    enabled: bool = False
    bind: str = "127.0.0.1"
    port: int = 0
    token: str | None = None
    static_dir: Path | None = None
    media_file: Path | None = None


@dataclass(frozen=True, slots=True)
class P6Config:
    schema_version: int
    source_mode: SourceMode
    sample_hz: float = 60.0
    queue_capacity: int = 2048
    tablet: TabletServerConfig = TabletServerConfig()
    aoi_file: Path | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise P6ConfigError("schema_version must be 1")
        if self.source_mode not in ("synthetic", "real"):
            raise P6ConfigError("source_mode must be synthetic or real")
        if self.sample_hz <= 0 or self.queue_capacity <= 0:
            raise P6ConfigError("sample_hz and queue_capacity must be positive")
        if self.tablet.bind != "127.0.0.1" and not self.tablet.token:
            raise P6ConfigError("LAN binding requires an explicit token")


def load_p6_config(path: str | Path) -> P6Config:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise P6ConfigError(f"cannot read P6 config: {error}") from error
    if not isinstance(raw, dict):
        raise P6ConfigError("P6 config must be a JSON object")
    allowed = {
        "schema_version",
        "source_mode",
        "sample_hz",
        "queue_capacity",
        "tablet",
        "aoi_file",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise P6ConfigError(f"unknown P6 config fields: {', '.join(unknown)}")
    tablet_raw = raw.get("tablet", {})
    if not isinstance(tablet_raw, dict):
        raise P6ConfigError("tablet must be a JSON object")
    tablet_allowed = {"enabled", "bind", "port", "token", "static_dir", "media_file"}
    tablet_unknown = sorted(set(tablet_raw) - tablet_allowed)
    if tablet_unknown:
        raise P6ConfigError(f"unknown tablet fields: {', '.join(tablet_unknown)}")
    try:
        tablet = TabletServerConfig(
            enabled=_boolean(tablet_raw.get("enabled", False), "tablet.enabled"),
            bind=_string(tablet_raw.get("bind", "127.0.0.1"), "tablet.bind"),
            port=_integer(tablet_raw.get("port", 0), "tablet.port"),
            token=_optional_string(tablet_raw.get("token"), "tablet.token"),
            static_dir=_optional_path(
                tablet_raw.get("static_dir"), "tablet.static_dir"
            ),
            media_file=_optional_path(
                tablet_raw.get("media_file"), "tablet.media_file"
            ),
        )
        return P6Config(
            schema_version=_integer(raw["schema_version"], "schema_version"),
            source_mode=_source_mode(raw["source_mode"]),
            sample_hz=_number(raw.get("sample_hz", 60.0), "sample_hz"),
            queue_capacity=_integer(raw.get("queue_capacity", 2048), "queue_capacity"),
            tablet=tablet,
            aoi_file=_optional_path(raw.get("aoi_file"), "aoi_file"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise P6ConfigError(f"invalid P6 config value: {error}") from error


def _string(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise P6ConfigError(f"{field} must be a string")
    return value


def _optional_string(value: JsonValue, field: str) -> str | None:
    return None if value is None else _string(value, field)


def _optional_path(value: JsonValue, field: str) -> Path | None:
    parsed = _optional_string(value, field)
    return None if parsed is None else Path(parsed)


def _boolean(value: JsonValue, field: str) -> bool:
    if not isinstance(value, bool):
        raise P6ConfigError(f"{field} must be a boolean")
    return value


def _integer(value: JsonValue, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise P6ConfigError(f"{field} must be an integer")
    return value


def _number(value: JsonValue, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise P6ConfigError(f"{field} must be a number")
    return float(value)


def _source_mode(value: JsonValue) -> SourceMode:
    parsed = _string(value, "source_mode")
    if parsed not in ("synthetic", "real"):
        raise P6ConfigError("source_mode must be synthetic or real")
    return parsed
