import json
import sys

import pytest

from src.p6.aoi_config import load_aoi_layout
from src.p6.source import RealSourceUnavailableError, SyntheticGazeSource
from src.p6.validation.mapping import ValidationError
from src.p6.validation.run import ValidationRun
from tools.tobii.aoi_validation import main


def test_no_device_preflight_retains_abort_logs_and_never_starts_carla(monkeypatch, tmp_path) -> None:
    def absent(source):
        raise RealSourceUnavailableError("NO_DEVICE")

    monkeypatch.setattr("tools.tobii.aoi_validation.TobiiProcessSource.discover", absent)

    result = main(["--tobii-python", sys.executable, "--tobii-serial", "FAKE-ABSENT",
                   "--aoi", "config/p6_aoi_candidate.json", "--preflight-only",
                   "--output-root", str(tmp_path), "--run-id", "no-device"])

    directory = tmp_path / "no-device"
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    evidence = json.loads((directory / "mapping_evidence.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    assert result == 2
    assert events[-1]["event"] == "ABORT"
    assert events[-1]["reason"] == "NO_DEVICE"
    assert evidence["window_geometry"] is None
    assert not evidence["display_mapping_verified"]
    assert summary["targets"]["FORWARD"]["total_samples"] == 0
    assert (directory / "raw_gaze.jsonl").is_file()
    assert (directory / "gaze_with_candidate_aoi.jsonl").is_file()


def test_existing_run_directory_is_never_overwritten(tmp_path) -> None:
    path = tmp_path / "existing"
    path.mkdir()

    with pytest.raises(FileExistsError):
        ValidationRun(path, load_aoi_layout("config/p6_aoi_candidate.json"))

    assert list(path.iterdir()) == []


def test_synthetic_sample_is_rejected_by_live_run(tmp_path) -> None:
    run = ValidationRun(tmp_path / "fake-test", load_aoi_layout("config/p6_aoi_candidate.json"))
    try:
        with pytest.raises(ValidationError, match="Synthetic"):
            run.sample(SyntheticGazeSource().emit(0))
    finally:
        run.sequence.abort(run.origin.monotonic_ns + 1, "UNIT_TEST")
        report = run.finish()

    assert report["aoi_validated"] is False
    assert run.sample_count == 0


def test_abort_persists_started_target_and_partial_window(tmp_path) -> None:
    run = ValidationRun(tmp_path / "fake-test", load_aoi_layout("config/p6_aoi_candidate.json"))
    origin = run.origin.monotonic_ns
    run.sequence.start(origin + 100)
    run.sequence.abort(origin + 1000, "ESC")

    run.finish()

    events = [json.loads(line) for line in (run.directory / "events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["PREPARE", "START", "ABORT"]
    assert all(event["expected_target"] == "FORWARD" for event in events)
    assert events[-1]["segment_index"] == 0
