from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.p6.cli import main
from src.p6.config import P6ConfigError, load_p6_config
from tests.test_p4_cli import p4_arguments


def write_config(path: Path, *, mode: str = "synthetic") -> Path:
    path.write_text(json.dumps({"schema_version": 1, "source_mode": mode}))
    return path


def test_config_parses_synthetic_and_rejects_unknown_fields(tmp_path: Path) -> None:
    config = load_p6_config(write_config(tmp_path / "p6.json"))
    assert config.source_mode == "synthetic"

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"schema_version": 1, "source_mode": "synthetic", "surprise": 1})
    )
    with pytest.raises(P6ConfigError, match="surprise"):
        load_p6_config(bad)

    wrong_type = tmp_path / "wrong-type.json"
    wrong_type.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_mode": "synthetic",
                "tablet": {"enabled": "false"},
            }
        )
    )
    with pytest.raises(P6ConfigError, match="boolean"):
        load_p6_config(wrong_type)


def test_preflight_real_reports_model_sdk_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = main(
        [
            "preflight",
            "--config",
            str(write_config(tmp_path / "real.json", mode="real")),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 2
    assert output["status"] == "BLOCKED_MODEL_SDK_UNCONFIRMED"


def test_synthetic_cli_writes_sidecar_without_hardware_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "gaze.jsonl"
    result = main(
        [
            "synthetic",
            "--output",
            str(output),
            "--run-id",
            "cli-run",
            "--samples",
            "3",
            "--sample-hz",
            "1000",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report["synthetic"] is True
    assert report["samples_requested"] == 3
    assert output.exists()


def test_research_parser_loads_optional_p6_config(tmp_path: Path) -> None:
    from src.scenario.research_noa import parse_arguments

    path = write_config(tmp_path / "p6.json")

    config = parse_arguments([*p4_arguments(tmp_path), "--p6-config", str(path)])

    assert config.p6 is not None
    assert config.p6.source_mode == "synthetic"
