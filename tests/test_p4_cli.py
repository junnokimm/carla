from __future__ import annotations

from pathlib import Path

import pytest

from src.experiment.context import Module1Condition
from src.scenario.research_noa import parse_arguments
from tests.test_research_noa_cli import SAFE_LIVE_CONTROL_ARGUMENTS


def assignment_file(tmp_path: Path) -> Path:
    path = tmp_path / "assignments.csv"
    path.write_text(
        "participant_id,m1_condition,m2_condition,route_id,scenario_version,"
        "aoi_file_version,program_version\n"
        "P001,SURT,NOA_L2,R1,S1,A1,V1\n",
        encoding="utf-8",
    )
    return path


def p4_arguments(tmp_path: Path) -> list[str]:
    return [
        "--live-smoke",
        "--spawn-index",
        "0",
        "--run-id",
        "run-001",
        "--assignment-file",
        str(assignment_file(tmp_path)),
        "--participant-id",
        "P001",
        "--output-dir",
        str(tmp_path / "logs"),
        "--automation-module",
        "MODULE_1",
        "--activation-center-tolerance-m",
        "0.2",
        "--activation-heading-tolerance-rad",
        "0.1",
        "--driver-brake-threshold",
        "0.05",
        "--button-deactivation",
        "enabled",
        "--interaction-stage",
        "development/module1-validation",
        *SAFE_LIVE_CONTROL_ARGUMENTS,
    ]


def test_p4_cli_derives_condition_from_assignment_row(tmp_path: Path) -> None:
    config = parse_arguments(p4_arguments(tmp_path))

    assert config.persistence is not None
    assert config.automation_interaction is not None
    assert config.automation_interaction.condition is Module1Condition.SURT
    assert config.persistence.assignment.route_id == "R1"


def test_p4_cli_rejects_condition_conflicting_with_assignment(
    tmp_path: Path, capsys
) -> None:
    arguments = p4_arguments(tmp_path)
    arguments.extend(("--automation-condition", "NO_SURT"))

    with pytest.raises(SystemExit) as caught:
        parse_arguments(arguments)

    assert caught.value.code == 2
    assert "conflicts" in capsys.readouterr().err
