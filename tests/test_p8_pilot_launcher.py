import json
from pathlib import Path

import pytest

from src.p8.operator_cli import _runtime_config, build_parser
from tools import run_pilot


def settings():
    return {
        "m1": "NO_SURT",
        "m2": "MANUAL",
        "m1_exit_open_s": 0.0,
        "m1_exit_close_s": 600.0,
        "m2_exit_open_s": 0.0,
        "m2_exit_close_s": 780.0,
    }


@pytest.mark.parametrize("m1", ["NO_SURT", "SURT"])
@pytest.mark.parametrize("m2", ["MANUAL", "NOA_L2"])
def test_pilot_launch_builds_real_nominal_p8_runtime(m1, m2):
    config = settings() | {"m1": m1, "m2": m2}
    argv = run_pilot.build_operator_args(config, participant_id="PILOT", run_id="RUN")
    args = build_parser().parse_args(argv)
    runtime = _runtime_config(args)
    assert args.training_duration == 600.0
    assert "--fullscreen" not in argv
    assert args.drive_duration is None
    assert (args.m1, args.m2) == (m1, m2)
    assert runtime.m2_hazard_v1 is not None
    assert runtime.module_exit.route_id == "town04-exit-47"
    assert runtime.training_exit.route_id == "town04-exit-47"
    assert runtime.module_exit.response_timeout_s == 15.0
    assert runtime.training_exit.response_timeout_s == 15.0
    assert runtime.tsxw_config.index == 0
    assert runtime.spawn_index == 344
    assert runtime.speed_policy is None
    assert runtime.control_config.longitudinal.target_speed_kmh is None


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), True, "15"])
def test_invalid_guidance_duration_is_rejected_before_launch(tmp_path, duration):
    path = tmp_path / "pilot.json"
    original = json.dumps(settings() | {"exit_guidance_duration_s": duration})
    path.write_text(original, encoding="utf-8")
    with pytest.raises((ValueError, TypeError), match="진출 안내 표시 시간"):
        run_pilot.load_pilot_settings(path)
    assert path.read_text(encoding="utf-8") == original


def test_configured_guidance_duration_reaches_training_and_modules(tmp_path):
    path = tmp_path / "pilot.json"
    original = json.dumps(settings() | {"exit_guidance_duration_s": 20.0})
    path.write_text(original, encoding="utf-8")
    loaded = run_pilot.load_pilot_settings(path)
    argv = run_pilot.build_operator_args(loaded, participant_id="PILOT", run_id="RUN")
    runtime = _runtime_config(build_parser().parse_args(argv))
    assert runtime.module_exit.response_timeout_s == 20.0
    assert runtime.training_exit.response_timeout_s == 20.0
    assert path.read_text(encoding="utf-8") == original


def test_first_launch_persists_only_explicit_missing_m2_window(tmp_path, monkeypatch):
    path = tmp_path / "pilot.json"
    original = settings() | {"m2_exit_open_s": None, "m2_exit_close_s": None}
    path.write_text(json.dumps(original), encoding="utf-8")
    responses = iter(("120", "360"))
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr("builtins.input", answer)
    chosen = run_pilot.load_pilot_settings(path)
    assert chosen["m2_exit_open_s"] == 120.0
    assert chosen["m2_exit_close_s"] == 360.0
    assert json.loads(path.read_text()) == chosen
    assert run_pilot.load_pilot_settings(path) == chosen
    assert len(prompts) == 2


def test_invalid_first_window_does_not_persist_or_launch(tmp_path, monkeypatch):
    path = tmp_path / "pilot.json"
    original = settings() | {"m2_exit_open_s": None, "m2_exit_close_s": None}
    path.write_text(json.dumps(original), encoding="utf-8")
    responses = iter(("0", "900"))
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    with pytest.raises(ValueError, match="780"):
        run_pilot.load_pilot_settings(path)
    assert json.loads(path.read_text()) == original


def test_empty_first_window_is_rejected_without_changing_config(tmp_path, monkeypatch):
    path = tmp_path / "pilot.json"
    original = settings() | {"m2_exit_open_s": None, "m2_exit_close_s": None}
    path.write_text(json.dumps(original), encoding="utf-8")
    responses = iter(("500", "500"))
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    with pytest.raises(ValueError, match="open은 close보다"):
        run_pilot.load_pilot_settings(path)
    assert json.loads(path.read_text()) == original


def test_config_publish_failure_preserves_original_and_does_not_launch(
    tmp_path, monkeypatch
):
    path = tmp_path / "pilot.json"
    original = settings() | {"m2_exit_open_s": None, "m2_exit_close_s": None}
    path.write_text(json.dumps(original), encoding="utf-8")
    responses = iter(("120", "360"))
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    def fail_replace(self, target):
        raise OSError("injected config publish failure")

    def unexpected_launch(argv):
        pytest.fail("Operator must not start when configuration cannot be saved")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(run_pilot.operator_cli, "main", unexpected_launch)
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    assert run_pilot.main(["--config", str(path)]) == 2
    assert json.loads(path.read_text()) == original
    assert list(tmp_path.iterdir()) == [path]


def test_repeated_launch_uses_new_ids_and_returns_operator_exit_code(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "pilot.json"
    config_path.write_text(json.dumps(settings()), encoding="utf-8")
    received = []

    def operator_main(argv):
        received.append(build_parser().parse_args(argv))
        return 2

    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_pilot.operator_cli, "main", operator_main)
    for _ in range(2):
        assert run_pilot.main(["--config", str(config_path), "--m1", "NO_SURT", "--m2", "NOA_L2"]) == 2
    assert received[0].participant_id != received[1].participant_id
    assert received[0].run_id != received[1].run_id
    assert all(args.m2 == "NOA_L2" for args in received)
    assert all(args.gaze_config == Path("config/p8_gaze.json") for args in received)
    assert json.loads(config_path.read_text())["m2"] == "MANUAL"
    assert Path.cwd() == tmp_path


@pytest.mark.parametrize("choice,expected", [("1", "MANUAL"), ("2", "NOA_L2")])
@pytest.mark.parametrize("interactive", [True, False])
def test_assignment_is_selected_per_run_without_changing_saved_config(
    tmp_path, monkeypatch, choice, expected, interactive
):
    path = tmp_path / "pilot.json"
    original = json.dumps(settings())
    path.write_text(original, encoding="utf-8")
    received = []
    replies = iter(("", "3", choice))
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        run_pilot.operator_cli, "main",
        lambda argv: received.append(build_parser().parse_args(argv)) or 0,
    )
    argv = ["--config", str(path), "--m1", "NO_SURT"]
    if not interactive:
        argv += ["--m2", choice]
    assert run_pilot.main(argv) == 0
    assert received[0].m2 == expected
    assert path.read_text(encoding="utf-8") == original
    if not interactive:
        assert next(replies) == ""  # Explicit option never prompts.


@pytest.mark.parametrize("error,code", [(EOFError, 2), (KeyboardInterrupt, 130)])
def test_cancelled_assignment_does_not_launch(tmp_path, monkeypatch, error, code):
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(settings()), encoding="utf-8")

    def cancel(_):
        raise error

    monkeypatch.setattr("builtins.input", cancel)
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_pilot.operator_cli, "main", lambda _: pytest.fail("launched"))
    assert run_pilot.main(["--config", str(path)]) == code
