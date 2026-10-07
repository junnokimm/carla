from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from src.experiment.context import Module1Condition, Module2Condition, ParticipantId

ASSIGNMENT_FIELDS: Final = (
    "participant_id",
    "m1_condition",
    "m2_condition",
    "route_id",
    "scenario_version",
    "aoi_file_version",
    "program_version",
)


class AssignmentError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ParticipantAssignment:
    participant_id: ParticipantId
    m1_condition: Module1Condition
    m2_condition: Module2Condition
    route_id: str
    scenario_version: str
    aoi_file_version: str
    program_version: str

    def __post_init__(self) -> None:
        values = (
            ("participant_id", self.participant_id),
            ("route_id", self.route_id),
            ("scenario_version", self.scenario_version),
            ("aoi_file_version", self.aoi_file_version),
            ("program_version", self.program_version),
        )
        for name, value in values:
            if not value.strip():
                raise AssignmentError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True)
class AssignmentTable:
    rows: tuple[ParticipantAssignment, ...]

    def lookup(self, participant_id: ParticipantId) -> ParticipantAssignment:
        for row in self.rows:
            if row.participant_id == participant_id:
                return row
        raise AssignmentError(f"participant_id {participant_id!r} is not assigned")


def load_assignment_csv(path: str | Path) -> AssignmentTable:
    source = Path(path)
    try:
        with source.open(newline="", encoding="utf-8-sig") as assignment_file:
            reader = csv.DictReader(assignment_file)
            if tuple(reader.fieldnames or ()) != ASSIGNMENT_FIELDS:
                raise AssignmentError(
                    f"assignment header must be {','.join(ASSIGNMENT_FIELDS)}"
                )
            parsed_rows: list[ParticipantAssignment] = []
            for line, row in enumerate(reader, 2):
                if None in row:
                    raise AssignmentError(f"line {line}: unexpected trailing columns")
                parsed_rows.append(_parse_row(row, line))
            rows = tuple(parsed_rows)
    except OSError as error:
        raise AssignmentError(f"cannot read assignment CSV: {source}") from error
    participant_ids = {row.participant_id for row in rows}
    if len(participant_ids) != len(rows):
        raise AssignmentError("participant_id values must be unique")
    return AssignmentTable(rows)


def _parse_row(row: dict[str, str | None], line: int) -> ParticipantAssignment:
    values: dict[str, str] = {}
    for field in ASSIGNMENT_FIELDS:
        value = row.get(field)
        if value is None or not value.strip():
            raise AssignmentError(f"line {line}: {field} must not be blank")
        values[field] = value.strip()
    try:
        return ParticipantAssignment(
            participant_id=ParticipantId(values["participant_id"]),
            m1_condition=Module1Condition(values["m1_condition"]),
            m2_condition=Module2Condition(values["m2_condition"]),
            route_id=values["route_id"],
            scenario_version=values["scenario_version"],
            aoi_file_version=values["aoi_file_version"],
            program_version=values["program_version"],
        )
    except ValueError as error:
        raise AssignmentError(f"line {line}: invalid condition") from error
