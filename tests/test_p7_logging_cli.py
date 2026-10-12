import ast
import csv
import json
from pathlib import Path

import pytest

from tools.p7_smoke import main


def test_dry_command_writes_p4_context_and_explicit_fake_provenance(tmp_path):
    result = main(["--mode", "dry", "--output-root", str(tmp_path / "runs"),
                   "--occurrences", str(tmp_path / "claims"), "--run-id", "p7-test"])
    directory = tmp_path / "runs/p7-test"
    with (directory / "research_events_p7-test.csv").open(newline="") as source:
        events = list(csv.DictReader(source))
    trigger = next(event for event in events if event["event_type"] == "hazard_trigger")
    payload = json.loads(trigger["event_value"])
    summary = json.loads((directory / "summary.json").read_text())

    assert result == 0
    assert summary["status"] == "COMPLETED"
    assert summary["hazard_validated"] is False
    assert trigger["participant_id"] == "DEV-P7-p7-test"
    assert trigger["run_id"] == "p7-test"
    assert trigger["module"] == "MODULE_2"
    assert trigger["phase"] == "FINAL_HAZARD_ASSESSMENT"
    assert trigger["carla_frame"]
    assert float(trigger["session_elapsed_s"]) >= 0
    assert payload["evidence_mode"] == "DRY_FAKE_INPUTS"
    assert payload["trigger_source"] == "DEV_LEAD_ROUTE_STATION"
    assert not {"tor", "collision_warning", "hazard_warning", "automatic_brake"} & {row["event_type"] for row in events}


def test_module_one_dry_command_has_no_hazard_sequence_or_claim(tmp_path):
    result = main(["--module", "MODULE_1", "--output-root", str(tmp_path / "runs"),
                   "--occurrences", str(tmp_path / "claims"), "--run-id", "module1"])

    summary = json.loads((tmp_path / "runs/module1/summary.json").read_text())
    assert result == 0
    assert not summary["hazard_actor_setup_called"]
    assert summary["traffic_count"] == 2
    assert not (tmp_path / "claims").exists()


def test_live_requires_explicit_opt_in_before_connection():
    with pytest.raises(SystemExit) as caught:
        main(["--mode", "live"])
    assert caught.value.code == 2


def test_p7_has_no_ego_controller_or_warning_actuation_calls():
    forbidden = {"apply_control", "set_autopilot", "request_control_mode", "set_driver_steering", "set_target_velocity"}
    root = Path(__file__).resolve().parents[1] / "src/p7"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not calls & forbidden, (path.name, calls & forbidden)
