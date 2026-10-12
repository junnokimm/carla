from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from tests.test_p6_tobii_protocol import gaze_record
from tools.tobii import calibration_smoke, discovery_smoke, import_gaze


def test_worker_dependencies_are_python310_syntax_and_do_not_import_carla() -> None:
    root = Path(__file__).resolve().parents[1] / "tools/tobii"
    for name in ("__init__", "sdk", "capture", "worker", "discovery_smoke", "gaze_smoke", "calibration_smoke"):
        tree = ast.parse((root / f"{name}.py").read_text(), feature_version=(3, 10))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(module and (module.startswith("src.") or module == "carla") for module in imports)


def test_discovery_supervisor_returns_explicit_no_sdk(monkeypatch, capsys) -> None:
    def no_sdk(command, **kwargs):
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 2, '{"status":"SDK_NOT_INSTALLED"}', "")

    monkeypatch.setattr(discovery_smoke.subprocess, "run", no_sdk)

    result = discovery_smoke.main([])

    assert result == 2
    assert capsys.readouterr().out.splitlines()[0] == "TOBII_DISCOVERY=SDK_NOT_INSTALLED"


def test_discovery_timeout_cannot_be_success(monkeypatch, capsys) -> None:
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(discovery_smoke.subprocess, "run", timeout)

    result = discovery_smoke.main([])

    assert result == 2
    assert capsys.readouterr().out.strip() == "TOBII_DISCOVERY=TIMEOUT"


def test_calibration_is_only_a_command_plan_without_execute(capsys) -> None:
    result = calibration_smoke.main(["--serial", "FAKE-001", "--screen", "2"])

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report["status"] == "NOT_RUN"
    assert report["command"][1:] == ["--mode=usercalibration", "--device-sn=FAKE-001", "--screen=2"]
    assert report["aoi_validated"] is False


def test_saved_smoke_reuses_p6_clock_writer_and_candidate_aoi(tmp_path, capsys) -> None:
    path = tmp_path / "raw.jsonl"
    rows = [{"record_type": "clock_pair", "sdk_system_us": i * 1_000_000,
             "host_before_ns": i * 1_000_000_000, "host_after_ns": i * 1_000_000_000} for i in range(5)]
    rows += [gaze_record(), {"record_type": "status", "status": "RAW_CAPTURED"}]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    output = tmp_path / "p6.jsonl"

    result = import_gaze.main(["--input", str(path), "--output", str(output),
                               "--run-id", "fake-smoke", "--origin-monotonic-ns", "0",
                               "--origin-utc-ns", "0", "--aoi", "config/p6_aoi_candidate.json"])

    report = json.loads(capsys.readouterr().out)
    recorded = [json.loads(line) for line in output.read_text().splitlines()]
    assert result == 0
    assert report["samples"] == 1
    assert report["hardware_validated"] is False
    assert recorded[1]["source_metadata"]["serial_number"] == "FAKE-001"
    assert recorded[1]["acquisition_session_seconds"] is None
    assert recorded[1]["estimated_session_seconds"] == 0.0002
    assert recorded[1]["mapping_verified"] is False
    assert report["software_clock_fit"] is True
    assert report["timing_quality_verified"] is False
    assert recorded[1]["aoi"] == "INVALID_LAYOUT"


def test_current_unsupported_interpreter_worker_exits_without_native_import() -> None:
    if sys.version_info[:2] == (3, 10):
        return

    result = subprocess.run([sys.executable, "-B", "-m", "tools.tobii.worker", "discover"],
                            capture_output=True, text=True, timeout=5, check=False)

    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "UNSUPPORTED_RUNTIME"
