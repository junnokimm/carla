from __future__ import annotations

import csv
import json
from pathlib import Path

from src.logging.csv_logger import RESEARCH_EVENT_HEADER
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_exit_ledger import ExitLedgerEvent


def exit_identity_conflict(
    path: Path,
    config: ResearchExitAssistanceConfig,
    events: tuple[ExitLedgerEvent, ...],
    participant_id: str | None,
) -> bool:
    stage = config.lc_event_id.split(":", 2)[1]
    expected_identity = {
        "TRAINING": (None, "TRAINING"),
        "MODULE_1_RUNNING": ("MODULE_1", "MODULE_1"),
        "MODULE_2_RUNNING": ("MODULE_2", "MODULE_2"),
    }.get(stage)
    if expected_identity is None:
        return True
    expected_module, expected_phase = expected_identity
    route_versions = {
        event.payload.get("route_version")
        for event in events
        if isinstance(event.payload.get("route_version"), str)
    }
    if len(route_versions) != 1:
        return True
    for event in events:
        payload = event.payload
        if payload.get("route_id") != config.route_id:
            return True
        if payload.get("context") != config.context.value:
            return True
        if (
            event.event_type != "p5_exit_manifest"
            and payload.get("module") != expected_module
        ):
            return True
    return _common_identity_conflict(
        path,
        config.lc_event_id,
        expected_module,
        expected_phase,
        participant_id,
    )


def _common_identity_conflict(
    path: Path,
    lc_event_id: str,
    expected_module: str | None,
    expected_phase: str,
    participant_id: str | None,
) -> bool:
    expected_run = lc_event_id.split(":", 1)[0]
    participants: set[str] = set()
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not set(RESEARCH_EVENT_HEADER).issubset(
            reader.fieldnames
        ):
            return participant_id is not None
        for row in reader:
            raw = row.get("event_value", "")
            if not raw.startswith("{"):
                continue
            payload = json.loads(raw)
            if payload.get("lc_event_id") != lc_event_id:
                continue
            if participant_id is not None and (
                row.get("study_run_id") != expected_run
                or row.get("run_id") != expected_run
                or row.get("participant_id") != participant_id
                or row.get("module") != (expected_module or "")
                or row.get("phase") != expected_phase
            ):
                return True
            for key in ("study_run_id", "run_id"):
                if (value := row.get(key)) and value != expected_run:
                    return True
            if participant := row.get("participant_id"):
                participants.add(participant)
            if (module := row.get("module")) and module != expected_module:
                return True
    return len(participants) > 1 or (
        participant_id is not None
        and bool(participants)
        and participants != {participant_id}
    )
