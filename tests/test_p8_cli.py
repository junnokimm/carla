import json
from pathlib import Path

import pytest

from src.p8.assignment import assigned_participant
from src.p8.preflight import inspect
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import FakeClient, FakeResearchVehicle, FakeResearchWorld
from tests.test_p8_live_drive import InteractiveViewerFactory
from tools.p8_smoke import main


@pytest.mark.parametrize("option,expected", [("--matrix", 4), ("--reset-smoke", 2)])
def test_accelerated_batch_cli_records_all_flows_without_hardware_claims(
    tmp_path, option, expected
):
    assert main(["--mode", "accelerated", option, "--output-root", str(tmp_path)]) == 0

    report = json.loads(next(tmp_path.glob("*/batch_summary.json")).read_text())
    assert len(report["runs"]) == expected
    assert report["participant_ready"] is False
    assert report["all_dev_flows_complete"]
    assert len({row["participant_id"] for row in report["runs"]}) == expected
    assert all(row["initial_state"]["modules"] == {} for row in report["runs"])


def test_abort_cli_returns_nonzero_and_keeps_index(tmp_path):
    assert (
        main(
            [
                "--mode",
                "accelerated",
                "--abort-at",
                "MODULE_2_RUNNING",
                "--output-root",
                str(tmp_path),
            ]
        )
        == 2
    )

    report = json.loads(next(tmp_path.glob("*/batch_summary.json")).read_text())
    assert report["runs"][0]["status"] == "ABORTED"
    assert Path(report["runs"][0]["manifest"]).exists()


def test_offline_preflight_never_calls_external_probe(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline preflight must not launch a device/server probe")

    monkeypatch.setattr("src.p8.preflight.subprocess.run", forbidden)

    assert (
        main(["--mode", "live-preflight", "--offline", "--output-root", str(tmp_path)])
        == 2
    )
    report = json.loads(next(tmp_path.glob("*/preflight.json")).read_text())
    assert report["participant_ready"] is False
    assert "clock_sync_ready" in report["blockers"]
    assert "p7_live_validated" in report["blockers"]
    assert report["checks"]["tobii_device"] == "NOT_PROBED_NO_HARDWARE_EXECUTION"


def test_preassigned_conditions_are_preserved_and_cannot_be_overridden(tmp_path):
    path = tmp_path / "assignment.csv"
    path.write_text(
        "participant_id,m1_condition,m2_condition,route_id,scenario_version,aoi_file_version,program_version\n"
        "P001,SURT,MANUAL,R1,S1,A1,V1\nP002,NO_SURT,NOA_L2,R1,S1,A1,V1\n"
    )

    assert assigned_participant(path, "P001").m2_condition.value == "MANUAL"
    assert assigned_participant(path, "P002").m2_condition.value == "NOA_L2"
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--mode",
                "accelerated",
                "--assignment-file",
                str(path),
                "--participant-id",
                "P001",
                "--m2",
                "NOA_L2",
            ]
        )
    assert caught.value.code == 2


def test_malformed_worker_metadata_is_explicitly_unavailable(monkeypatch, tmp_path):
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        "src.p8.preflight.subprocess.run",
        lambda command, **kwargs: CompletedProcess(command, 0, "{}", ""),
    )

    report = inspect(
        tmp_path / "probe",
        readiness_file=Path("config/p8_readiness.json"),
        tobii_python=Path("fake-python"),
        host="fake",
        port=1,
        offline=False,
    )

    assert report["checks"]["tobii_worker"]["status"] == "UNAVAILABLE"
    assert report["participant_ready"] is False


def test_carla_version_warning_does_not_hide_valid_read_only_probe(
    monkeypatch, tmp_path
):
    from subprocess import CompletedProcess

    def response(command, **kwargs):
        payload = (
            {"python": [3, 10], "bits": 64, "sdk": "2.1.0"}
            if "-c" in command
            else {
                "status": "CONNECTED_READ_ONLY",
                "map": "FAKE",
                "assets": {"static.prop.box01": True},
            }
        )
        return CompletedProcess(
            command,
            0,
            "WARNING: fake version diagnostic\n" + json.dumps(payload) + "\n",
            "",
        )

    monkeypatch.setattr("src.p8.preflight.subprocess.run", response)

    report = inspect(
        tmp_path / "probe",
        readiness_file=Path("config/p8_readiness.json"),
        tobii_python=Path("fake-python"),
        host="fake",
        port=1,
        offline=False,
    )

    assert report["checks"]["carla"]["status"] == "CONNECTED_READ_ONLY"
    assert (
        report["checks"]["tobii_worker"]["status"]
        == "WORKER_PACKAGE_AVAILABLE_NOT_DEVICE_VALIDATION"
    )
    assert report["participant_ready"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["--mode", "dev-live-drive", "--ack-dev-live", "--spawn-index", "-1"],
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--duration",
            "0",
        ],
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--duration",
            "nan",
        ],
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--duration",
            "61",
        ],
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--host",
            "",
        ],
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--port",
            "0",
        ],
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--port",
            "65536",
        ],
        ["--mode", "accelerated", "--duration", "1"],
        ["--mode", "accelerated", "--ack-dev-live"],
        ["--mode", "accelerated", "--front-camera-only"],
        ["--mode", "accelerated", "--host", "127.0.0.1"],
        ["--mode", "accelerated", "--port", "2000"],
        ["--mode", "accelerated", "--noa-max-throttle", "1"],
    ],
)
def test_invalid_live_cli_options_fail_before_artifacts_or_runtime_import(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        main([*arguments, "--output-root", str(tmp_path)])

    assert caught.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "extra",
    [
        ["--noa-desired-speed-kmh", "25"],
        ["--dev-speed-limit-kmh", "30"],
        ["--ack-high-speed-dev"],
        ["--noa-desired-speed-kmh", "25", "--dev-speed-limit-kmh", "35"],
        ["--noa-desired-speed-kmh", "10", "--ack-high-speed-dev"],
        ["--dev-speed-limit-kmh", "30", "--ack-high-speed-dev"],
        ["--noa-desired-speed-kmh", "10", "--dev-speed-limit-kmh", "10"],
        [
            "--noa-desired-speed-kmh",
            "10",
            "--dev-speed-limit-kmh",
            "110",
            "--ack-high-speed-dev",
        ],
        ["--noa-max-throttle", "0"],
        ["--noa-max-throttle", "nan"],
        ["--noa-max-throttle", "1.01"],
        ["--noa-max-throttle", "1"],
        [
            "--noa-max-throttle",
            "1",
            "--noa-desired-speed-kmh",
            "10",
            "--dev-speed-limit-kmh",
            "30",
        ],
    ],
)
def test_high_speed_cli_requires_complete_bounded_explicit_opt_in_before_artifacts(
    tmp_path: Path,
    extra: list[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--mode",
                "dev-live-drive",
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


def test_dev_live_cli_runs_fake_backed_path_without_real_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_runner(config, *, borrowed_log):
        return ResearchNoARunner(
            config,
            client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
            viewer_factory=InteractiveViewerFactory(),
            borrowed_log=borrowed_log,
        )

    monkeypatch.setattr(
        "src.scenario.research_noa_runtime.ResearchNoARunner",
        fake_runner,
    )

    exit_code = main(
        [
            "--mode",
            "dev-live-drive",
            "--ack-dev-live",
            "--spawn-index",
            "0",
            "--duration",
            "1",
            "--output-root",
            str(tmp_path),
        ]
    )

    manifest = json.loads(next(tmp_path.glob("*/session_manifest.json")).read_text())
    assert exit_code == 0
    assert manifest["completion_status"] == "DEV_LIVE_PARTIAL_COMPLETE"
    assert manifest["provenance"] == "DEV_STUB"
    assert manifest["sources"]["carla"] == "INJECTED_TEST_CLIENT"


@pytest.mark.parametrize(
    ("extra", "desired", "guard", "profile", "max_throttle"),
    [
        (["--noa-desired-speed-kmh", "10"], 10.0, 20.0, "LEGACY_DEV", 0.4),
        (
            [
                "--noa-desired-speed-kmh",
                "10",
                "--dev-speed-limit-kmh",
                "30",
                "--ack-high-speed-dev",
            ],
            10.0,
            30.0,
            "HIGH_SPEED_DEV_UNVALIDATED",
            1.0,
        ),
        (
            [
                "--noa-desired-speed-kmh",
                "25",
                "--dev-speed-limit-kmh",
                "35",
                "--ack-high-speed-dev",
            ],
            25.0,
            35.0,
            "HIGH_SPEED_DEV_UNVALIDATED",
            1.0,
        ),
    ],
)
def test_dev_live_cli_persists_explicit_speed_policy_without_targeting_maximum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra: list[str],
    desired: float,
    guard: float,
    profile: str,
    max_throttle: float,
) -> None:
    def fake_runner(config, *, borrowed_log):
        return ResearchNoARunner(
            config,
            client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
            viewer_factory=InteractiveViewerFactory(),
            borrowed_log=borrowed_log,
        )

    monkeypatch.setattr(
        "src.scenario.research_noa_runtime.ResearchNoARunner",
        fake_runner,
    )

    assert (
        main(
            [
                "--mode",
                "dev-live-drive",
                "--ack-dev-live",
                "--spawn-index",
                "0",
                "--duration",
                "1",
                *extra,
                *(
                    ["--noa-max-throttle", "1"]
                    if profile == "HIGH_SPEED_DEV_UNVALIDATED"
                    else []
                ),
                "--output-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    manifest = json.loads(next(tmp_path.glob("*/session_manifest.json")).read_text())
    assert manifest["control_config"]["longitudinal"]["target_speed_kmh"] == desired
    assert manifest["speed_policy"]["desired_speed_kmh"] == desired
    assert manifest["speed_policy"]["effective_desired_speed_kmh"] == desired
    assert manifest["control_config"]["longitudinal"]["max_throttle"] == max_throttle
    assert manifest["speed_policy"]["max_allowed_speed_kmh"] == 110.0
    assert manifest["speed_policy"]["operational_guard_speed_kmh"] == guard
    assert manifest["speed_policy"]["profile"] == profile
    assert manifest["participant_ready"] is False
