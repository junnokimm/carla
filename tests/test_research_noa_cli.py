from __future__ import annotations

import pytest

from tests.test_noa_runtime import make_control_config

EXPLICIT_CONTROL_ARGUMENTS = [
    "--target-speed-kmh",
    "36",
    "--speed-deadband-kmh",
    "1",
    "--acceleration-gain",
    "0.1",
    "--braking-gain",
    "0.1",
    "--max-throttle",
    "0.6",
    "--max-brake",
    "0.7",
    "--lateral-error-gain",
    "0.2",
    "--heading-error-gain",
    "0.5",
    "--lateral-deadband-m",
    "0.1",
    "--heading-deadband-rad",
    "0.05",
    "--max-steering",
    "0.8",
]

SAFE_LIVE_CONTROL_ARGUMENTS = [
    "--target-speed-kmh",
    "20",
    "--speed-deadband-kmh",
    "1",
    "--acceleration-gain",
    "0.1",
    "--braking-gain",
    "0.1",
    "--max-throttle",
    "0.25",
    "--max-brake",
    "0.5",
    "--lateral-error-gain",
    "0.2",
    "--heading-error-gain",
    "0.5",
    "--lateral-deadband-m",
    "0.1",
    "--heading-deadband-rad",
    "0.05",
    "--max-steering",
    "0.15",
]


def test_cli_requires_every_controller_tuning_value(capsys) -> None:
    from src.scenario.research_noa import parse_arguments

    with pytest.raises(SystemExit) as caught:
        parse_arguments(["--dry-run", *EXPLICIT_CONTROL_ARGUMENTS[:-2]])

    assert caught.value.code == 2
    assert "--max-steering" in capsys.readouterr().err


def test_cli_builds_explicit_validated_control_config() -> None:
    from src.scenario.research_noa import parse_arguments

    config = parse_arguments(
        ["--dry-run", "--duration", "1", *EXPLICIT_CONTROL_ARGUMENTS]
    )

    assert config.duration == 1.0
    assert config.control_config == make_control_config()
    assert config.vehicle_blueprint == "vehicle.mercedes.coupe_2020"


def test_dry_run_preserves_duration_without_live_smoke_cap() -> None:
    from src.scenario.research_noa import parse_arguments

    config = parse_arguments(
        ["--dry-run", "--duration", "30", *EXPLICIT_CONTROL_ARGUMENTS]
    )

    assert config.duration == 30.0


def test_cli_reports_existing_controller_validation_error(capsys) -> None:
    from src.scenario.research_noa import parse_arguments

    arguments = EXPLICIT_CONTROL_ARGUMENTS.copy()
    arguments[arguments.index("--acceleration-gain") + 1] = "0"

    with pytest.raises(SystemExit) as caught:
        parse_arguments(["--dry-run", *arguments])

    assert caught.value.code == 2
    assert "acceleration_gain must be finite and > 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mode_arguments",
    [
        [],
        ["--dry-run", "--live-smoke", "--spawn-index", "0"],
    ],
)
def test_cli_requires_exactly_one_run_mode(mode_arguments, capsys) -> None:
    from src.scenario.research_noa import parse_arguments

    with pytest.raises(SystemExit) as caught:
        parse_arguments([*mode_arguments, *SAFE_LIVE_CONTROL_ARGUMENTS])

    assert caught.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err or not mode_arguments


def test_live_smoke_requires_explicit_spawn_index(capsys) -> None:
    from src.scenario.research_noa import parse_arguments

    with pytest.raises(SystemExit) as caught:
        parse_arguments(["--live-smoke", *SAFE_LIVE_CONTROL_ARGUMENTS])

    assert caught.value.code == 2
    assert "--spawn-index is required with --live-smoke" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "unsafe_value", "expected_message"),
    [
        ("--duration", "20.0001", "duration must be <= 20.0"),
        ("--duration", "20.1", "duration must be <= 20.0"),
        ("--duration", "21.0", "duration must be <= 20.0"),
        ("--duration", "30", "duration must be <= 20.0"),
        ("--target-speed-kmh", "20.01", "target_speed_kmh must be <= 20.0"),
        ("--max-throttle", "0.251", "max_throttle must be <= 0.25"),
        ("--max-brake", "0.501", "max_brake must be <= 0.5"),
        ("--max-steering", "0.151", "max_steering must be <= 0.15"),
    ],
)
def test_live_smoke_rejects_values_above_safety_caps(
    option,
    unsafe_value,
    expected_message,
    capsys,
) -> None:
    from src.scenario.research_noa import parse_arguments

    arguments = [
        "--live-smoke",
        "--spawn-index",
        "0",
        "--duration",
        "5",
        *SAFE_LIVE_CONTROL_ARGUMENTS,
    ]
    if option in arguments:
        arguments[arguments.index(option) + 1] = unsafe_value
    else:
        arguments.extend((option, unsafe_value))

    with pytest.raises(SystemExit) as caught:
        parse_arguments(arguments)

    assert caught.value.code == 2
    assert expected_message in capsys.readouterr().err


@pytest.mark.parametrize("duration", [5.0, 10.0, 20.0])
def test_live_smoke_accepts_duration_windows_and_safety_boundaries(
    duration: float,
) -> None:
    from src.scenario.research_noa import (
        ResearchNoARunConfig,
        ResearchNoARunMode,
        parse_arguments,
    )
    from src.scenario.research_noa_config import ResearchNoAConfigError

    config = parse_arguments(
        [
            "--live-smoke",
            "--spawn-index",
            "3",
            "--duration",
            str(duration),
            *SAFE_LIVE_CONTROL_ARGUMENTS,
        ]
    )

    assert config.mode is ResearchNoARunMode.LIVE_SMOKE
    assert config.spawn_index == 3
    assert config.duration == duration

    with pytest.raises(ResearchNoAConfigError) as caught:
        ResearchNoARunConfig(
            duration=1.0,
            control_config=config.control_config,
            mode=ResearchNoARunMode.LIVE_SMOKE,
        )

    assert caught.value.field == "spawn_index"
    assert caught.value.requirement == "is required for live smoke"
