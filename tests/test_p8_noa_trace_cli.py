from __future__ import annotations

import csv
import json
from itertools import chain, repeat
from pathlib import Path

import pytest

from src.experiment.automation_interaction import DriverInput
from src.scenario.research_noa_config import ResearchNoARunConfig
from src.scenario.research_noa_log_setup import OpenResearchLog
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import FakeClient, FakeResearchVehicle, FakeResearchWorld
from tests.test_p8_live_drive import InteractiveViewerFactory
from tools.p8_smoke import main


def test_high_cli_preserves_default_cap_and_wires_pi_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_runner(
        config: ResearchNoARunConfig, *, borrowed_log: OpenResearchLog
    ) -> ResearchNoARunner:
        inputs = chain((DriverInput(activation_requested=True),), repeat(DriverInput()))
        return ResearchNoARunner(
            config,
            client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
            viewer_factory=InteractiveViewerFactory(),
            driver_input_source=inputs.__next__,
            borrowed_log=borrowed_log,
        )

    monkeypatch.setattr(
        "src.scenario.research_noa_runtime.ResearchNoARunner", fake_runner
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
            "--noa-desired-speed-kmh",
            "25",
            "--dev-speed-limit-kmh",
            "35",
            "--ack-high-speed-dev",
            "--pi-trace",
            "--output-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    manifest_path = next(tmp_path.glob("*/session_manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["control_config"]["longitudinal"]["max_throttle"] == 0.4
    assert manifest["speed_policy"]["desired_speed_kmh"] == 25.0
    assert "cached_readback_throttle" in capsys.readouterr().out
    event_path = next(manifest_path.parent.glob("research_events_*.csv"))
    with event_path.open(newline="", encoding="utf-8") as source:
        transition = next(
            row
            for row in csv.DictReader(source)
            if row["event_type"] == "noa_control_saturation_transition"
        )
    payload = json.loads(transition["event_value"])
    assert payload["commanded"]["throttle"] == 0.4
    assert payload["saturated"]["throttle"] is True
