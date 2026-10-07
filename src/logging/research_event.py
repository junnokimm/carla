from __future__ import annotations

from dataclasses import dataclass

from src.logging.research_csv_error import ResearchCsvError


@dataclass(frozen=True, slots=True)
class ResearchEvent:
    event_type: str
    event_value: str | None = None

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ResearchCsvError("event_type must not be blank")
