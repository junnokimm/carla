from __future__ import annotations

import json
from math import inf, nan
from pathlib import Path

import pytest

from src.experiment.exit_assistance import ExitContext
from src.p8.operator_cli import build_parser, main

SPEED_OPTIONS: tuple[str, ...] = ()

P5_BEHAVIOR_OPTIONS = (
    "--exit-navigation-trigger-distance-m",
    "1000",
    "--exit-response-timeout-s",
    "5",
    "--exit-lateral-onset-threshold-m",
    "0.15",
    "--m1-exit-open-s",
    "120",
    "--m1-exit-close-s",
    "180",
    "--m2-exit-open-s",
    "240",
    "--m2-exit-close-s",
    "300",
)


def test_cli_surface_uses_traffic_manager_default_speed_without_custom_throttle() -> (
    None
):
    option_strings = {
        option for action in build_parser()._actions for option in action.option_strings
    }

    assert "--noa-desired-speed-kmh" not in option_strings
    assert "--noa-max-throttle" not in option_strings
    assert "--traffic-manager-port" in option_strings
    assert "--dev-speed-limit-kmh" not in option_strings
    assert "--ack-high-speed-dev" not in option_strings


def test_dev_cli_requires_ack_and_spawn_before_ui_or_artifacts(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                *SPEED_OPTIONS,
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


def test_cli_defaults_rear_off_training_600_and_nominal_module_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[tuple[bool, float, float | None]] = []

    def fake_ui(controller, config):
        observed.append(
            (
                controller.runtime.rear_mirror_enabled,
                config.training_duration_s,
                config.drive_duration_s,
            )
        )
        return 2

    monkeypatch.setattr("src.p8.operator_cli.run_operator_ui", fake_ui)

    result = main(
        [
            *SPEED_OPTIONS,
            "--participant-id",
            "R-1",
            "--run-id",
            "run-1",
            "--m1",
            "NO_SURT",
            "--m2",
            "MANUAL",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--output-root",
            str(tmp_path),
        ]
    )

    assert result == 2
    assert observed == [(False, 600.0, None)]


def test_cli_builds_explicit_module_and_training_exit_templates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed = []

    def fake_ui(controller, config):
        observed.append((controller.runtime, config.registration))
        return 0

    monkeypatch.setattr("src.p8.operator_cli.run_operator_ui", fake_ui)
    manifest = Path("config/town04_exit_routes_dev_v3.json")

    result = main(
        [
            *SPEED_OPTIONS,
            "--participant-id",
            "R-1",
            "--run-id",
            "run-1",
            "--m1",
            "NO_SURT",
            "--m2",
            "MANUAL",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--exit-route-manifest",
            str(manifest),
            "--exit-route-id",
            "town04-exit-39",
            "--training-exit-route-manifest",
            str(manifest),
            "--training-exit-route-id",
            "town04-exit-39",
            *P5_BEHAVIOR_OPTIONS,
            "--output-root",
            str(tmp_path),
        ]
    )

    runtime, registration = observed[0]
    assert result == 0
    assert runtime.module_exit is not None
    assert runtime.module_exit.context is ExitContext.MODULE
    assert runtime.training_exit is not None
    assert runtime.training_exit.context is ExitContext.TRAINING
    assert runtime.module_exit.confirm_key == "x"
    assert runtime.module_exit.reject_key == "r"
    assert runtime.module_exit.assisted_execution_enabled is True
    assert runtime.module_1_exit_window is not None
    assert runtime.module_1_exit_window.open_s == 120.0
    assert runtime.module_2_exit_window is not None
    assert runtime.module_2_exit_window.close_s == 300.0
    assert registration.route_id == "town04-exit-39"


def test_cli_rejects_module_route_without_researcher_timing_windows(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                "--exit-route-manifest",
                "config/town04_exit_routes_dev_v3.json",
                "--exit-route-id",
                "town04-exit-39",
                "--exit-navigation-trigger-distance-m",
                "1000",
                "--exit-response-timeout-s",
                "5",
                "--exit-lateral-onset-threshold-m",
                "0.15",
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--m1-exit-close-s", "600.001"),
        ("--m2-exit-close-s", "780.001"),
    ],
)
def test_cli_rejects_module_window_beyond_nominal_duration(
    option: str,
    value: str,
    tmp_path: Path,
) -> None:
    timing = list(P5_BEHAVIOR_OPTIONS)
    timing[timing.index(option) + 1] = value

    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                "--exit-route-manifest",
                "config/town04_exit_routes_dev_v3.json",
                "--exit-route-id",
                "town04-exit-39",
                *timing,
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "extra",
    [
        ("--exit-route-manifest", "config/town04_exit_routes_dev_v3.json"),
        ("--exit-route-id", "town04-exit-39"),
        (
            "--exit-route-manifest",
            "config/town04_exit_routes_dev_v3.json",
            "--exit-route-id",
            "town04-exit-39",
        ),
    ],
)
def test_cli_rejects_partial_or_behaviorless_exit_configuration_before_artifacts(
    extra: tuple[str, ...],
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                *SPEED_OPTIONS,
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                *extra,
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


def test_cli_rejects_drive_duration_with_module_exit_before_ui_or_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reached_ui: list[bool] = []
    monkeypatch.setattr(
        "src.p8.operator_cli.run_operator_ui",
        lambda controller, config: reached_ui.append(True),
    )

    with pytest.raises(SystemExit) as caught:
        main(
            [
                *SPEED_OPTIONS,
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                "--drive-duration",
                "1",
                "--exit-route-manifest",
                "config/town04_exit_routes_dev_v3.json",
                "--exit-route-id",
                "town04-exit-39",
                *P5_BEHAVIOR_OPTIONS,
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert reached_ui == []
    assert not any(tmp_path.iterdir())


def test_cli_rejects_orphan_exit_behavior_before_ui_or_artifacts(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                *SPEED_OPTIONS,
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                *P5_BEHAVIOR_OPTIONS,
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "option,value",
    [
        ("--training-duration", "600.001"),
        ("--drive-duration", "60.001"),
        ("--drive-duration", "600"),
    ],
)
def test_cli_rejects_duration_above_separate_operational_caps(
    option: str,
    value: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                *SPEED_OPTIONS,
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                option,
                value,
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("timeout", [nan, inf, -inf])
def test_cli_rejects_non_finite_timeout_before_ui_or_artifacts(
    timeout: float,
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                *SPEED_OPTIONS,
                "--participant-id",
                "R-1",
                "--run-id",
                "run-1",
                "--m1",
                "NO_SURT",
                "--m2",
                "MANUAL",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                "--timeout",
                str(timeout),
                "--output-root",
                str(tmp_path),
            ]
        )

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


def test_participant_registration_blocks_before_runner_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assignment = tmp_path / "assignment.csv"
    assignment.write_text(
        "participant_id,m1_condition,m2_condition,route_id,scenario_version,aoi_file_version,program_version\n"
        "P001,NO_SURT,MANUAL,R1,S1,A1,V1\n",
        encoding="utf-8",
    )
    builds: list[str] = []

    def forbidden_runner(*args, **kwargs):
        builds.append("built")
        raise AssertionError("runner must not be constructed")

    def attempt_registration(controller, config):
        controller.register_id(config.registration)
        return 0

    monkeypatch.setattr("src.p8.operator_cli.ResearchNoARunner", forbidden_runner)
    monkeypatch.setattr("src.p8.operator_cli.run_operator_ui", attempt_registration)

    result = main(
        [
            *SPEED_OPTIONS,
            "--mode",
            "participant",
            "--participant-id",
            "P001",
            "--run-id",
            "participant-run",
            "--assignment-file",
            str(assignment),
            "--output-root",
            str(tmp_path / "output"),
        ]
    )

    assert result == 2
    assert builds == []


def test_keyboard_interrupt_after_registration_aborts_and_persists_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def interrupted_ui(controller, config):
        controller.register_id(config.registration)
        raise KeyboardInterrupt

    monkeypatch.setattr("src.p8.operator_cli.run_operator_ui", interrupted_ui)

    result = main(
        [
            *SPEED_OPTIONS,
            "--participant-id",
            "R-1",
            "--run-id",
            "run-1",
            "--m1",
            "NO_SURT",
            "--m2",
            "MANUAL",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--output-root",
            str(tmp_path),
        ]
    )

    manifests = list(tmp_path.rglob("session_manifest.json"))
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert result == 130
    assert len(manifests) == 1
    assert manifest["state"] == "ABORTED"
    assert manifest["cleanup"]["completed"] is True
