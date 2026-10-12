import hashlib
import json
from pathlib import Path

from src.p7.types import P7Error


class OccurrenceRegistry:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def claim(self, participant_id: str, run_id: str) -> None:
        if not participant_id.strip() or not run_id.strip():
            raise P7Error("INVALID_OCCURRENCE_IDENTITY")
        self.directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(participant_id.encode("utf-8")).hexdigest()
        try:
            with (self.directory / f"{key}.json").open("x", encoding="utf-8") as output:
                json.dump({"participant_id": participant_id, "run_id": run_id,
                           "module": "MODULE_2", "status": "RESERVED_NO_AUTOMATIC_RETRY"}, output)
        except FileExistsError as error:
            raise P7Error("HAZARD_ALREADY_RESERVED_OR_EXECUTED") from error
