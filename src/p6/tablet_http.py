from __future__ import annotations

import json
import math
from dataclasses import dataclass

from src.p6.tablet import TabletProtocolError
from src.p6.tablet_models import JsonValue

type PayloadValue = str | int | float | bool | None
MAX_JSON_BYTES = 32_768
STREAM_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int


def parse_json_object(body: bytes) -> dict[str, PayloadValue]:
    def reject_constant(value: str) -> None:
        raise TabletProtocolError(f"non-finite JSON number is not allowed: {value}")

    value = json.loads(body, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise TabletProtocolError("JSON body must be an object")
    return value


def encode_json(payload: dict[str, JsonValue]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()


def text(payload: dict[str, PayloadValue], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise TabletProtocolError(f"{field} must be a string")
    return value


def optional_text(payload: dict[str, PayloadValue], field: str) -> str | None:
    value = payload.get(field)
    return None if value is None else text(payload, field)


def number(payload: dict[str, PayloadValue], field: str) -> float:
    value = payload[field]
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise TabletProtocolError(f"{field} must be a finite number")
    return float(value)


def boolean(payload: dict[str, PayloadValue], field: str) -> bool:
    value = payload[field]
    if not isinstance(value, bool):
        raise TabletProtocolError(f"{field} must be a boolean")
    return value


def parse_byte_range(header: str | None, size: int) -> ByteRange:
    if header is None:
        return ByteRange(0, size - 1)
    if not header.startswith("bytes=") or "," in header:
        raise TabletProtocolError("only one byte range is supported")
    first, separator, last = header.removeprefix("bytes=").partition("-")
    if separator != "-" or not first or not first.isdecimal():
        raise TabletProtocolError("invalid byte range")
    start = int(first)
    if last and not last.isdecimal():
        raise TabletProtocolError("invalid byte range")
    end = size - 1 if not last else min(int(last), size - 1)
    if start > end or start >= size:
        raise TabletProtocolError("byte range is outside the media")
    return ByteRange(start, end)
