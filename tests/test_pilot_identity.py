import csv
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from zipfile import ZipFile

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p7.types import P7Error
from src.p8.crash_recovery import recover_stopped_run
from src.p8.model import Mode, P8Error
from src.p8.operator_cli import build_parser
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import RegistrationRequest
from src.p8.operator_registration import claim_registration
from src.p8.pilot_identity import participant_number, reserve_pilot_run
from tests.test_p8_operator_backend import RunnerEvidence, StrictClock, runtime_config
from tests.test_p8_pilot_launcher import settings
from tools import run_pilot


@pytest.mark.parametrize(
    "value,expected", [("001", "001"), ("2", "002"), (" 013 ", "013"), ("1234", "1234")]
)
def test_number_keeps_leading_zeros(value, expected):
    assert participant_number(value) == expected


@pytest.mark.parametrize(
    "value", ["", "../001", "1/2", "1\\2", "CON", "１２３", "-1", "1.0", "1234567"]
)
def test_invalid_number_never_becomes_a_path(value):
    with pytest.raises(ValueError):
        participant_number(value)


def test_name_reservation_preserves_old_directories_archives_and_abandoned_claims(
    tmp_path,
):
    (tmp_path / "001_test").mkdir()
    exports = tmp_path / "_exports"
    exports.mkdir()
    archive = exports / "001_test_02.zip"
    archive.write_bytes(b"previous result")
    assert reserve_pilot_run(tmp_path, "001", "test") == "001_test_03"
    assert reserve_pilot_run(tmp_path, "001", "test") == "001_test_04"
    assert reserve_pilot_run(tmp_path, "001", "pilot") == "001_pilot"
    assert archive.read_bytes() == b"previous result"
    assert not (tmp_path / "001_test_03").exists()  # session owns directory creation


def test_concurrent_launches_cannot_claim_the_same_output(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(
            pool.map(lambda _: reserve_pilot_run(tmp_path, "001", "test"), range(16))
        )
    assert len(set(names)) == 16
    assert set(names) == {"001_test", *(f"001_test_{i:02d}" for i in range(2, 17))}


def test_full_terminal_input_order_and_raw_id(tmp_path, monkeypatch, capsys):
    config = tmp_path / "pilot.json"
    original = json.dumps(settings())
    config.write_text(original, encoding="utf-8")
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    prompts, received = [], []
    answers = iter(["", "../oops", "001", "bad", "2", "1", "2"])

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(
        run_pilot.operator_cli,
        "main",
        lambda argv: received.append(build_parser().parse_args(argv)) or 0,
    )
    assert run_pilot.main(["--config", str(config)]) == 0
    args = received[0]
    assert (args.participant_id, args.run_id, args.session_type) == (
        "001",
        "001_pilot",
        "pilot",
    )
    assert args.m1 == "SURT" and args.m2 == "NOA_L2"
    assert "참가자" in prompts[0] and "저장 유형" in prompts[3]
    assert "M1" in prompts[-2] and "M2" in prompts[-1]
    assert "001_pilot.zip" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("failure", ["bad_number", "storage"])
def test_invalid_identity_or_failed_reservation_never_starts_drive(
    tmp_path, monkeypatch, failure
):
    config = tmp_path / "pilot.json"
    config.write_text(json.dumps(settings()), encoding="utf-8")
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        run_pilot.operator_cli, "main", lambda _: pytest.fail("drive started")
    )
    if failure == "storage":

        def unavailable(*args):
            raise OSError("name reservation disk failure")

        monkeypatch.setattr(run_pilot, "reserve_pilot_run", unavailable)
    assert (
        run_pilot.main(
            [
                "--config",
                str(config),
                "--participant-id",
                "abc" if failure == "bad_number" else "001",
                "--session-type",
                "test",
                "--m1",
                "2",
                "--m2",
                "1",
            ]
        )
        == 2
    )


def request(run_id="001_test", session_type="test"):
    return RegistrationRequest(
        "001",
        run_id,
        m1_condition=Module1Condition.NO_SURT,
        m2_condition=Module2Condition.MANUAL,
        session_type=session_type,
    )


def test_new_pilot_registration_never_reuses_run_or_weakens_participant_policy(
    tmp_path,
):
    claim_registration(tmp_path, request(), Mode.DEV_REHEARSAL)
    claim_registration(tmp_path, request("001_test_02"), Mode.DEV_REHEARSAL)
    with pytest.raises(P8Error, match="RUN_ID_ALREADY_CLAIMED"):
        claim_registration(tmp_path, request(), Mode.DEV_REHEARSAL)
    with pytest.raises(P8Error, match="SESSION_TYPE_REQUIRES"):
        claim_registration(tmp_path, request("001_pilot", "pilot"), Mode.PARTICIPANT)
    legacy = replace(request("legacy"), session_type=None)
    claim_registration(tmp_path, legacy, Mode.DEV_REHEARSAL)
    with pytest.raises(P8Error, match="PARTICIPANT_ID_ALREADY_CLAIMED"):
        claim_registration(
            tmp_path, replace(legacy, run_id="legacy2"), Mode.DEV_REHEARSAL
        )


def test_repeat_participant_preserves_each_zip_ids_and_once_per_run_hazard_guard(
    tmp_path,
):
    old_archives = []
    for kind, expected in [
        ("test", "001_test"),
        ("test", "001_test_02"),
        ("pilot", "001_pilot"),
    ]:
        run_id = reserve_pilot_run(tmp_path, "001", kind)
        assert run_id == expected
        clock = StrictClock()
        controller = OperatorController(
            tmp_path,
            mode=Mode.DEV_REHEARSAL,
            runtime=runtime_config(clock, RunnerEvidence([], [], [], [])),
            monotonic_ns=clock.monotonic_ns,
            utc_ns=clock.utc_ns,
        )
        controller.register_id(request(run_id, kind))
        # A separate run can experience its own hazard, never a second hazard
        # within the same run. No geometry/timing/obstacle logic is replaced.
        registry = controller.session.occurrences
        registry.claim("001", run_id)
        with pytest.raises(P7Error, match="HAZARD_ALREADY"):
            registry.claim("001", run_id)
        controller.abort("offline named output check")
        result = controller.session.result_export
        assert result.error is None
        assert result.archive.name == expected + ".zip"
        with ZipFile(result.archive) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read(f"{run_id}/session_manifest.json"))
            assert manifest["participant_id"] == "001" and manifest["run_id"] == run_id
            assert manifest["session_type"] == kind
            assert manifest["result_archive_basename"] == expected + ".zip"
            assert manifest["state"] == "ABORTED" and not manifest["analysis_eligible"]
        with next(result.directory.glob("research_events_*.csv")).open(
            encoding="utf-8", newline=""
        ) as stream:
            rows = list(csv.DictReader(stream))
        assert rows and {row["participant_id"] for row in rows} == {"001"}
        assert {row["run_id"] for row in rows} == {run_id}
        for path, original in old_archives:
            assert path.read_bytes() == original
        old_archives.append((result.archive, result.archive.read_bytes()))


def test_crash_recovery_uses_same_number_type_and_keeps_partial_status(tmp_path):
    run_id = reserve_pilot_run(tmp_path, "002", "pilot")
    directory = tmp_path / run_id
    directory.mkdir()
    manifest = {
        "participant_id": "002",
        "run_id": run_id,
        "session_type": "pilot",
        "result_archive_basename": run_id + ".zip",
        "state": "MODULE_2_RUNNING",
    }
    (directory / "session_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    raw = directory / "original.bin"
    raw.write_bytes(b"preserve partial evidence")
    result = recover_stopped_run(directory, returncode=23, worker_exited=True)
    assert result.error is None and result.archive.name == "002_pilot.zip"
    with ZipFile(result.archive) as archive:
        saved = json.loads(archive.read(f"{run_id}/session_manifest.json"))
        assert saved["participant_id"] == "002" and saved["session_type"] == "pilot"
        assert (
            saved["completion_status"] == "ABORTED" and not saved["analysis_eligible"]
        )
        assert archive.read(f"{run_id}/original.bin") == raw.read_bytes()
