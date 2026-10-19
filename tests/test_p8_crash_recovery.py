import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from src.p8.crash_recovery import atomic_json, recover_stopped_run
from tools import run_pilot_supervised
from tools.run_pilot_supervised import ROOT, require_registered_worker_exit, supervise


def unfinished(root):
    directory = root / "run-crash"
    directory.mkdir(parents=True)
    atomic_json(directory / "session_manifest.json", {
        "run_id": directory.name, "state": "MODULE_2_RUNNING", "completion_status": "RUNNING",
        "stages": [{"stage": "MODULE_1_RUNNING", "status": "COMPLETED", "duration_s": 600},
                   {"stage": "MODULE_2_RUNNING", "status": "RUNNING", "start_session_s": 812}],
        "components": {"module_runs": [{"stage": "MODULE_1_RUNNING", "window_complete": True}],
                       "hazard_state": "SCHEDULED"}})
    with (directory / "research_events.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["run_id", "session_elapsed_s", "module", "event_type", "event_value"])
        writer.writerow([directory.name, 815, "MODULE_2", "m2_hazard_scheduled", '{"scheduled": 440}'])
        writer.writerow([directory.name, 820, "MODULE_2", "m2_hazard_raw_samples", "x" * 200000])
        stream.write(f"{directory.name},821")  # abruptly cut row: preserve, don't count
    pending = directory / ".gaze_pending/MODULE_2_RUNNING-test"
    pending.mkdir(parents=True)
    interval = {"record_type": "gaze_interval", "state": "TABLET_BINOCULAR", "duration_s": 0.25,
                "start_clock_ns": 1000000000, "end_clock_ns": 1250000000, "session_end_s": 820.25}
    (pending / "intervals.jsonl").write_text(json.dumps(interval) + '\n{"record_type":', encoding="utf-8")
    (pending / "gaze.jsonl").write_text('{"record_type":"gaze"}\n', encoding="utf-8")
    return directory


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_recovery_preserves_raw_partial_records_and_does_not_complete_m2(tmp_path):
    directory = unfinished(tmp_path)
    originals = {p: digest(p) for p in directory.rglob("*") if p.is_file()}
    result = recover_stopped_run(directory, returncode=0xc0000409, worker_exited=True)
    assert result.error is None
    manifest = json.loads((directory / "session_manifest.json").read_text())
    assert manifest["state"] == manifest["completion_status"] == "ABORTED"
    assert not manifest["analysis_eligible"]
    assert manifest["stages"][0]["status"] == "COMPLETED"
    assert manifest["stages"][1]["duration_s"] is None
    assert manifest["components"]["module_runs"][0]["window_complete"]
    assert manifest["components"]["hazard_state"] == "SCHEDULED"
    report = json.loads((directory / "crash_recovery/report.json").read_text())
    assert report["worker_exit_code_hex"] == "0xC0000409"
    assert report["last_saved_session_s"] == 820
    assert report["csv"]["research_events.csv"]["rows"] == 2
    assert report["csv"]["research_events.csv"]["invalid_rows"] == 1
    intervals = next(v for k, v in report["pending_gaze"].items() if k.endswith("intervals.jsonl"))
    assert intervals["seconds_by_state"] == {"TABLET_BINOCULAR": 0.25}
    assert intervals["invalid_records"] == 1
    for path, before in originals.items():
        if path.name == "session_manifest.json":
            assert digest(directory / "crash_recovery/session_manifest.before_recovery.json") == before
        else:
            assert digest(path) == before
    with ZipFile(result.archive) as archive:
        assert archive.testzip() is None
        name = next(n for n in archive.namelist() if n.endswith("intervals.jsonl"))
        assert archive.read(name).endswith(b'{"record_type":')
    before_zip = digest(result.archive)
    assert recover_stopped_run(directory, returncode=23, worker_exited=True) == result
    assert digest(result.archive) == before_zip


def test_recovery_refuses_active_writer_and_wrong_context(tmp_path):
    directory = unfinished(tmp_path)
    with pytest.raises(ValueError, match="EXITED_WORKER"):
        recover_stopped_run(directory, returncode=23, worker_exited=False)
    atomic_json(directory / "session_manifest.json", {"run_id": "another-run"})
    with pytest.raises(ValueError, match="RUN_ID_MISMATCH"):
        recover_stopped_run(directory, returncode=23, worker_exited=True)


def test_export_failure_retains_evidence_and_retry_can_finish(tmp_path, monkeypatch):
    directory = unfinished(tmp_path)
    original = ZipFile.write
    monkeypatch.setattr(ZipFile, "write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    result = recover_stopped_run(directory, returncode=23, worker_exited=True)
    assert result.archive is None and "disk full" in result.error
    assert (directory / "crash_recovery/report.json").is_file()
    monkeypatch.setattr(ZipFile, "write", original)
    assert recover_stopped_run(directory, returncode=23, worker_exited=True).error is None


@pytest.mark.parametrize("exit_code", [23, -1073740791])
def test_separate_supervisor_recovers_real_abrupt_child_without_finally(tmp_path, exit_code):
    directory = unfinished(tmp_path)
    notices = []
    script = """
import os, sys
from pathlib import Path
from src.p8.crash_recovery import register_supervised_run
register_supervised_run(Path(sys.argv[1]), 'PILOT')
print('partial output without newline', end='', flush=True)
try:
    os._exit(int(sys.argv[2]))
finally:
    Path(sys.argv[1], 'finally-ran').touch()
"""
    code = supervise([sys.executable, "-B", "-c", script, str(directory), str(exit_code)],
                     output_root=tmp_path, cwd=ROOT, notice=notices.append)
    assert code & 0xffffffff == exit_code & 0xffffffff
    assert not (directory / "finally-ran").exists()
    assert len(notices) == 1 and "부분 기록" in notices[0]
    assert (tmp_path / "_exports/run-crash.zip").is_file()
    logs = list((tmp_path / "_supervisor").glob("*/console.log"))
    assert logs[0].read_bytes() == b"partial output without newline"


@pytest.mark.parametrize("exit_code", [0, 2, 130])
def test_normal_exit_has_no_crash_notice_and_preserves_exit_code(tmp_path, exit_code):
    notices = []
    assert supervise([sys.executable, "-B", "-c", f"raise SystemExit({exit_code})"],
                     output_root=tmp_path, cwd=ROOT, notice=notices.append) == exit_code
    assert not notices


def test_supervisor_rejects_registration_for_another_run(tmp_path):
    directory = unfinished(tmp_path / "other")
    before = digest(directory / "session_manifest.json")
    script = "from pathlib import Path; from src.p8.crash_recovery import register_supervised_run; import sys; register_supervised_run(Path(sys.argv[1]), 'P'); raise SystemExit(23)"
    notices = []
    assert supervise([sys.executable, "-B", "-c", script, str(directory)],
                     output_root=tmp_path, cwd=ROOT, notice=notices.append) == 23
    assert "REGISTRATION_MISMATCH" in notices[0]
    assert digest(directory / "session_manifest.json") == before


def test_supervisor_never_imports_carla_or_pygame():
    script = "import sys; import tools.run_pilot_supervised; assert 'carla' not in sys.modules; assert 'pygame' not in sys.modules"
    subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, check=True)


def test_supervisor_cannot_recover_its_own_live_process():
    with pytest.raises(ValueError, match="INVALID_REGISTERED_WORKER_PID"):
        require_registered_worker_exit(os.getpid())


def test_prompt_and_unicode_are_forwarded_without_extra_carriage_returns(tmp_path, capsys):
    script = "import sys; sys.stdout.buffer.write('M2 배정: \\r\\n1 또는 2: '.encode('utf-8')); sys.stdout.flush()"
    assert supervise([sys.executable, "-B", "-c", script], output_root=tmp_path,
                     cwd=ROOT, notice=lambda _: pytest.fail("unexpected notice")) == 0
    captured = capsys.readouterr().out
    assert "M2 배정: \n1 또는 2: " in captured
    assert "\r" not in captured


def test_exit_log_failure_still_recovers_and_notifies(tmp_path, monkeypatch):
    directory = unfinished(tmp_path)
    notices = []
    script = "from pathlib import Path; from src.p8.crash_recovery import register_supervised_run; import os,sys; register_supervised_run(Path(sys.argv[1]), 'P'); os._exit(23)"
    monkeypatch.setattr(run_pilot_supervised, "atomic_json",
                        lambda *a: (_ for _ in ()).throw(OSError("exit log unavailable")))
    assert supervise([sys.executable, "-B", "-c", script, str(directory)],
                     output_root=tmp_path, cwd=ROOT, notice=notices.append) == 23
    assert len(notices) == 1 and "부분 기록" in notices[0]
    assert (tmp_path / "_exports/run-crash.zip").is_file()


def test_unterminated_telemetry_field_is_not_counted_as_a_complete_row(tmp_path):
    directory = unfinished(tmp_path)
    (directory / "telemetry.csv").write_text(
        "run_id,session_elapsed_s,module,indicator\nrun-crash,900,MODULE_2,o", encoding="utf-8")
    assert recover_stopped_run(directory, returncode=23, worker_exited=True).error is None
    report = json.loads((directory / "crash_recovery/report.json").read_text())
    assert report["csv"]["telemetry.csv"]["rows"] == 0
    assert report["csv"]["telemetry.csv"]["invalid_rows"] == 1
    assert report["last_saved_session_s"] == 820


@pytest.mark.skipif(os.name != "nt", reason="Windows native exit status")
def test_cli_preserves_signed_native_exit_status(monkeypatch):
    monkeypatch.setattr(run_pilot_supervised, "supervise", lambda *a, **k: 0xc0000409)
    assert run_pilot_supervised.main([]) == -1073740791
