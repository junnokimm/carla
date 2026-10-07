from dataclasses import replace
from pathlib import Path

import pytest

from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_cli import parse_arguments
from src.scenario.research_noa_config import (
    ResearchNoAConfigError,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from tests.test_p4_cli import p4_arguments
from tests.test_p4_research_noa_integration import make_config
from tests.test_research_noa_cli import SAFE_LIVE_CONTROL_ARGUMENTS


def p5_config(tmp_path: Path) -> ResearchNoARunConfig:
    return replace(
        make_config(tmp_path),
        exit_assistance=ResearchExitAssistanceConfig(
            Path("config/town04_exit_routes_dev_v3.json"),
            "town04-exit-39",
            "development-exit",
        ),
    )


def p5_arguments(tmp_path: Path) -> list[str]:
    return [
        *p4_arguments(tmp_path),
        "--exit-route-id", "town04-exit-39",
        "--exit-event-id", "development-exit",
    ]


@pytest.mark.parametrize("duration", [20.0, 20.01, 60.0, 120.0])
def test_explicit_p5_development_duration_is_accepted(tmp_path: Path, duration: float) -> None:
    config = replace(p5_config(tmp_path), duration=duration, development_validation=True)
    assert config.duration == duration
    assert config.development_validation is True


@pytest.mark.parametrize("duration", [120.001, 121.0, 0.0, -1.0, float("nan"), float("inf")])
def test_development_duration_rejects_invalid_or_over_limit(tmp_path: Path, duration: float) -> None:
    with pytest.raises(ResearchNoAConfigError, match="duration"):
        replace(p5_config(tmp_path), duration=duration, development_validation=True)


def test_default_p5_live_smoke_still_has_twenty_second_cap(tmp_path: Path) -> None:
    config = replace(p5_config(tmp_path), duration=20.0)
    assert config.development_validation is False
    with pytest.raises(ResearchNoAConfigError, match="20.0"):
        replace(config, duration=20.001)


@pytest.mark.parametrize("mode", [ResearchNoARunMode.DRY_RUN, ResearchNoARunMode.LIVE_SMOKE])
def test_override_is_not_available_without_live_p5(tmp_path: Path, mode: ResearchNoARunMode) -> None:
    config = make_config(tmp_path)
    if mode is ResearchNoARunMode.DRY_RUN:
        config = replace(config, mode=mode, automation_interaction=None, persistence=None)
    with pytest.raises(ResearchNoAConfigError, match="development_validation"):
        replace(config, development_validation=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [("target_speed_kmh", 20.01), ("max_throttle", 0.4001), ("max_brake", 0.5001), ("max_steering", 0.1501)],
)
def test_override_never_relaxes_control_caps(tmp_path: Path, field: str, value: float) -> None:
    config = p5_config(tmp_path)
    controls = config.control_config
    if field == "max_steering":
        controls = replace(controls, lateral=replace(controls.lateral, max_steering=value))
    else:
        controls = replace(controls, longitudinal=replace(controls.longitudinal, **{field: value}))
    with pytest.raises(ResearchNoAConfigError, match=field):
        replace(config, duration=120.0, development_validation=True, control_config=controls)


@pytest.mark.parametrize("duration", ["20.01", "120"])
def test_cli_accepts_explicit_development_override(tmp_path: Path, duration: str) -> None:
    config = parse_arguments([
        *p5_arguments(tmp_path), "--development-validation", "--duration", duration,
    ])
    assert config.duration == float(duration)
    assert config.development_validation is True


@pytest.mark.parametrize(
    ("extra", "message"),
    [(["--duration", "21"], "20.0"),
     (["--development-validation", "--duration", "120.001"], "120.0")],
)
def test_cli_rejects_duration_outside_selected_cap(tmp_path: Path, capsys, extra: list[str], message: str) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([*p5_arguments(tmp_path), *extra])
    assert caught.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--dry-run", "--live-smoke"])
def test_cli_rejects_override_outside_p5(mode: str, capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([mode, "--spawn-index", "0", *SAFE_LIVE_CONTROL_ARGUMENTS, "--development-validation"])
    assert caught.value.code == 2
    assert "development_validation" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value"),
    [("--target-speed-kmh", "20.01"), ("--max-throttle", "0.4001"),
     ("--max-brake", "0.5001"), ("--max-steering", "0.1501")],
)
def test_cli_override_keeps_other_caps(tmp_path: Path, capsys, option: str, value: str) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([
            *p5_arguments(tmp_path), "--development-validation", "--duration", "120", option, value,
        ])
    assert caught.value.code == 2
    assert option[2:].replace("-", "_") in capsys.readouterr().err
