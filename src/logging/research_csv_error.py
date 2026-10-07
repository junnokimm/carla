from __future__ import annotations


class ResearchCsvError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
