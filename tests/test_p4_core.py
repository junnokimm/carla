from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.experiment.assignment import AssignmentError, load_assignment_csv
from src.experiment.context import (
    Module1Condition,
    Module2Condition,
    ParticipantId,
    StudyRunContext,
    StudyRunId,
)
from src.experiment.session_clock import SessionClock, SessionClockError
from src.experiment.timestamp import HostClockTimestamp
from src.logging.csv_logger import ResearchCsvError, ResearchCsvLogger

HEADER = (
    "participant_id,m1_condition,m2_condition,route_id,scenario_version,"
    "aoi_file_version,program_version\n"
)


def write_assignments(path: Path, rows: str) -> None:
    path.write_text(HEADER + rows, encoding="utf-8")


def study_run(run_id: str) -> StudyRunContext:
    return StudyRunContext(
        StudyRunId(run_id),
        ParticipantId("P001"),
        Module1Condition.NO_SURT,
        Module2Condition.NOA_L2,
    )


def test_session_clock_starts_at_zero_and_uses_host_monotonic_ns() -> None:
    clock = SessionClock(HostClockTimestamp(1_000_000_000, 100))

    assert clock.elapsed_seconds(HostClockTimestamp(1_000_000_000, 999)) == 0.0
    assert clock.elapsed_seconds(HostClockTimestamp(1_250_000_000, 1)) == 0.25


def test_session_clock_accepts_equal_readings_as_nondecreasing() -> None:
    clock = SessionClock(HostClockTimestamp(10, 10))

    assert clock.elapsed_seconds(HostClockTimestamp(10, 9)) == 0.0
    assert clock.elapsed_seconds(HostClockTimestamp(10, 8)) == 0.0


def test_session_clock_rejects_backwards_reading() -> None:
    clock = SessionClock(HostClockTimestamp(10, 10))
    clock.elapsed_seconds(HostClockTimestamp(20, 20))

    with pytest.raises(SessionClockError, match="backwards"):
        clock.elapsed_seconds(HostClockTimestamp(19, 30))


def test_assignment_loader_parses_independent_module_conditions(tmp_path: Path) -> None:
    path = tmp_path / "assignments.csv"
    write_assignments(path, "P001,SURT,MANUAL,R1,S1,A1,V1\n")

    row = load_assignment_csv(path).lookup(ParticipantId("P001"))

    assert row.m1_condition is Module1Condition.SURT
    assert row.m2_condition is Module2Condition.MANUAL
    assert row.route_id == "R1"


def test_assignment_loader_rejects_duplicate_participants(tmp_path: Path) -> None:
    path = tmp_path / "assignments.csv"
    write_assignments(
        path,
        "P001,SURT,MANUAL,R1,S1,A1,V1\nP001,NO_SURT,NOA_L2,R2,S2,A2,V2\n",
    )

    with pytest.raises(AssignmentError, match="unique"):
        load_assignment_csv(path)


@pytest.mark.parametrize(
    "row",
    (
        "P001,INVALID,MANUAL,R1,S1,A1,V1\n",
        "P001,SURT,INVALID,R1,S1,A1,V1\n",
        "P001,SURT,MANUAL,,S1,A1,V1\n",
        "P001,SURT,MANUAL,R1,S1,A1,V1,EXTRA\n",
        "P001,SURT,MANUAL,R1,S1,A1,V1,\n",
    ),
)
def test_assignment_loader_rejects_malformed_rows(tmp_path: Path, row: str) -> None:
    path = tmp_path / "assignments.csv"
    write_assignments(path, row)

    with pytest.raises(AssignmentError):
        load_assignment_csv(path)


def test_assignment_lookup_rejects_unknown_participant(tmp_path: Path) -> None:
    path = tmp_path / "assignments.csv"
    write_assignments(path, "P001,SURT,MANUAL,R1,S1,A1,V1\n")

    with pytest.raises(AssignmentError, match="not assigned"):
        load_assignment_csv(path).lookup(ParticipantId("P999"))


def test_logger_rejects_unsafe_run_id_before_creating_files(tmp_path: Path) -> None:
    with pytest.raises(ResearchCsvError, match="path-safe"):
        ResearchCsvLogger(study_run("../escape"), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_same_participant_can_use_distinct_run_ids(tmp_path: Path) -> None:
    first = ResearchCsvLogger(study_run("run-001"), tmp_path)
    second = ResearchCsvLogger(study_run("run-002"), tmp_path)
    first.close()
    second.close()

    assert len(list(tmp_path.glob("research_*.csv"))) == 4


def test_logger_headers_flush_before_close(tmp_path: Path) -> None:
    logger = ResearchCsvLogger(study_run("run-001"), tmp_path)

    with logger.event_path.open(newline="") as event_file:
        rows = list(csv.reader(event_file))

    assert len(rows) == 1
    logger.close()
