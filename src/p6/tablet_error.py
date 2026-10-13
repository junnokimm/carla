from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TabletProtocolError(ValueError):
    requirement: str

    def __str__(self) -> str:
        return self.requirement
