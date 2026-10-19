from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pygame
import pytest

from src.p8.model import Stage
from src.p8.operator_cli import _print_result_paths
from src.p8.operator_pages import stage_page
from src.p8.operator_results import OperatorResultExport, export_operator_results
from src.p8.operator_ui import OperatorApp
from src.p8.operator_ui_runtime import run_operator_ui
from tests.test_p8_operator_backend import StrictClock, register_dev
from tests.test_p8_operator_ui import FakeController, config


def terminal_directory(root: Path, state: str = "SESSION_COMPLETE") -> Path:
    directory = root / "run-export"
    directory.mkdir()
    (directory / "session_manifest.json").write_text(
        json.dumps({"run_id": directory.name, "state": state}), encoding="utf-8"
    )
    (directory / "research_events_run-export.csv").write_bytes(b"event\nrecorded\n")
    (directory / "nested").mkdir()
    (directory / "nested" / "diagnostic.json").write_bytes(b'{"observed": true}')
    return directory


@pytest.mark.parametrize("state", ["SESSION_COMPLETE", "ABORTED"])
def test_export_contains_only_finalized_run_and_preserves_exact_originals(
    tmp_path: Path, state: str
) -> None:
    directory = terminal_directory(tmp_path, state)
    (tmp_path / "another-run.csv").write_bytes(b"must not be exported")
    originals = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }

    result = export_operator_results(directory, closed=True)

    assert result.error is None
    assert result.archive == tmp_path / "_exports" / "run-export.zip"
    assert result.archive.parent != directory
    with ZipFile(result.archive) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(originals)
        for name, content in originals.items():
            assert archive.read(name) == content
            assert (tmp_path / name).read_bytes() == content


def test_repeated_export_reuses_identical_zip_but_never_replaces_different_data(
    tmp_path: Path,
) -> None:
    directory = terminal_directory(tmp_path)
    first = export_operator_results(directory, closed=True)
    original_zip = first.archive.read_bytes()
    original_stat = first.archive.stat()

    assert export_operator_results(directory, closed=True) == first
    assert first.archive.stat().st_mtime_ns == original_stat.st_mtime_ns
    (directory / "nested" / "diagnostic.json").write_bytes(b'{"observed": false}')
    refused = export_operator_results(directory, closed=True)

    assert refused.archive is None
    assert "RESULT_ZIP_EXISTS_WITH_DIFFERENT_CONTENT" in refused.error
    assert first.archive.read_bytes() == original_zip


@pytest.mark.parametrize("closed,state", [(False, "ABORTED"), (True, "RUNNING")])
def test_export_refuses_open_logs_or_nonterminal_manifest(
    tmp_path: Path, closed: bool, state: str
) -> None:
    directory = terminal_directory(tmp_path, state)

    result = export_operator_results(directory, closed=closed)

    assert result.archive is None
    assert result.error is not None
    assert not (tmp_path / "_exports").exists()


def test_export_failure_preserves_source_and_reports_no_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = terminal_directory(tmp_path)

    def disk_failure(*args, **kwargs):
        raise OSError("injected export storage failure")

    monkeypatch.setattr("src.p8.operator_results.ZipFile.write", disk_failure)
    result = export_operator_results(directory, closed=True)

    assert result.archive is None
    assert "injected export storage failure" in result.error
    assert (directory / "session_manifest.json").is_file()
    assert (directory / "nested" / "diagnostic.json").is_file()
    assert list((tmp_path / "_exports").iterdir()) == []


def test_export_does_not_follow_link_to_another_run(tmp_path: Path) -> None:
    directory = terminal_directory(tmp_path)
    outside = tmp_path / "other-run.csv"
    outside.write_bytes(b"must remain outside the export")
    try:
        (directory / "linked.csv").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is not permitted in this environment")

    result = export_operator_results(directory, closed=True)

    assert result.archive is None
    assert "RESULT_EXPORT_CANNOT_INCLUDE_SYMLINKS" in result.error
    assert outside.read_bytes() == b"must remain outside the export"


def test_actual_operator_abort_exports_terminal_manifest_and_live_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    controller, _ = register_dev(tmp_path, StrictClock())

    controller.abort("result export regression")
    result = controller.snapshot().result_export
    manifest = controller.session.services.directory / "session_manifest.json"
    _print_result_paths(controller)
    printed = json.loads(capsys.readouterr().out)

    assert result.error is None
    assert printed["results_directory"] == str(result.directory)
    assert printed["results_archive"] == str(result.archive)
    assert printed["session_state"] == "ABORTED"
    with ZipFile(result.archive) as archive:
        assert (
            archive.read(f"{result.directory.name}/session_manifest.json")
            == manifest.read_bytes()
        )
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["state"] == "ABORTED"
    assert saved["participant_ready"] is False
    assert saved["analysis_eligible"] is False
    assert saved["completeness"]["files"]
    assert all(
        item["provenance"] == controller.session.services.provenance
        for item in saved["completeness"]["files"]
    )
    controller.abort("ignored repeated abort")
    assert controller.snapshot().result_export is result


def test_final_screen_shows_actual_hazard_record_and_export_failure(
    tmp_path: Path,
) -> None:
    snapshot = replace(
        FakeController(Stage.SESSION_COMPLETE).snapshot(),
        result_export=OperatorResultExport(tmp_path, None, "OSError:disk unavailable"),
        hazard_status="DEVELOPMENT_RUN_ENDED / RETIRED",
    )

    page = stage_page(snapshot, "")

    assert "ZIP 내보내기 실패" in page.title
    assert any(str(tmp_path) in line for line in page.body)
    assert any("OSError:disk unavailable" in line for line in page.body)
    assert any("DEVELOPMENT_RUN_ENDED / RETIRED" in line for line in page.body)
    assert not any("hazard 통합은 차단" in line for line in page.body)


def test_ui_does_not_return_success_when_result_export_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = FakeController(Stage.SESSION_COMPLETE)
    snapshot = replace(
        controller.snapshot(),
        result_export=OperatorResultExport(tmp_path, None, "OSError:disk unavailable"),
    )
    monkeypatch.setattr(controller, "snapshot", lambda: snapshot)
    monkeypatch.setattr(
        OperatorApp, "start", lambda self: setattr(self, "registered", True)
    )
    monkeypatch.setattr(pygame.event, "get", lambda: [pygame.event.Event(pygame.QUIT)])

    assert run_operator_ui(controller, config()) == 2
