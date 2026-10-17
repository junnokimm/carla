from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pygame

from src.p8.model import Mode, Stage
from src.p8.operator_controller import OperatorController
from src.p8.operator_ui import OperatorApp
from tests.test_p8_operator_backend import RunnerEvidence, StrictClock, runtime_config
from tests.test_p8_operator_ui import config


def test_real_pygame_event_driver_completes_truthful_dev_flow(tmp_path: Path) -> None:
    evidence_path = os.environ.get("P8_OPERATOR_EVIDENCE_DIR")
    output = Path(evidence_path) if evidence_path else tmp_path
    output.mkdir(parents=True, exist_ok=True)
    clock = StrictClock()
    runner = RunnerEvidence([], [], [], [])
    controller = OperatorController(
        tmp_path / "session",
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime_config(clock, runner),
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    app = OperatorApp(controller, config(1.0, training_duration=1.0))
    pygame.init()
    surface = pygame.Surface((1280, 720))
    trace: list[dict[str, str | float]] = []

    def press(key: int, label: str) -> None:
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=key)])
        app.handle_events([pygame.event.Event(pygame.KEYUP, key=key)])
        stage = (
            "UNREGISTERED" if not app.registered else controller.snapshot().stage.value
        )
        trace.append({"event": label, "stage": stage, "status": app.status})

    def capture(name: str) -> None:
        app.render(surface)
        pygame.image.save(surface, output / name)

    try:
        app.start()
        capture("01-tobii-external-calibration.png")
        press(pygame.K_SPACE, "record external unconnected calibration boundary")
        capture("02-module-1-instruction.png")
        press(pygame.K_SPACE, "acknowledge exact M1 instruction")
        capture("03-rest.png")
        launches_before_rest = len(runner.viewers)
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        trace.append(
            {
                "event": "advance immediately from rest with held repeat rejected",
                "stage": controller.snapshot().stage.value,
                "status": app.status,
            }
        )
        capture("04-module-2-instruction.png")
        assert len(runner.viewers) == launches_before_rest
        app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
        press(pygame.K_SPACE, "acknowledge exact M2 instruction")
        capture("05-session-saved.png")
    finally:
        pygame.quit()
    rest_record = next(
        record
        for record in controller.session.records
        if record["stage"] == Stage.REST.value
    )
    (output / "event-driver.json").write_text(
        json.dumps(
            {
                "events": trace,
                "runner_launches": len(runner.viewers),
                "rest_transition_stage": next(
                    item["stage"]
                    for item in trace
                    if item["event"]
                    == "advance immediately from rest with held repeat rejected"
                ),
                "rest_timing": {
                    "start_session_s": rest_record["start_session_s"],
                    "end_session_s": rest_record["end_session_s"],
                    "duration_s": rest_record["duration_s"],
                    "minimum_s": rest_record["minimum_s"],
                    "recommended_duration_s": rest_record["recommended_duration_s"],
                },
                "cleanup_completed": controller.session.cleanup_report["completed"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert app.running is True
    assert len(runner.viewers) == 3
    assert controller.snapshot().stage is Stage.SESSION_COMPLETE
    assert controller.session.cleanup_report["completed"] is True
    exported = controller.snapshot().result_export
    assert exported.error is None
    assert exported.archive.is_file()
    with ZipFile(exported.archive) as archive:
        archived_manifest = archive.read(
            f"{exported.directory.name}/session_manifest.json"
        )
    assert (
        archived_manifest == (exported.directory / "session_manifest.json").read_bytes()
    )
    records = {record["stage"]: record for record in controller.session.records}
    rest = records[Stage.REST.value]
    assert rest["minimum_s"] == 0
    assert rest["recommended_duration_s"] == 120
    assert rest["duration_s"] == rest["end_session_s"] - rest["start_session_s"]
    assert 0 <= rest["duration_s"] < 120
    consent = records[Stage.CONSENT_AND_ELIGIBILITY.value]
    training = records[Stage.TRAINING.value]
    post = records[Stage.POST_EXPERIMENT.value]
    assert consent["receipts"] == {}
    assert training["receipts"] == {}
    assert post["receipts"] == {}
    assert consent["execution_policy_outcome"]["outcome"] == "SKIPPED"
    assert training["execution_policy_outcome"]["outcome"] == "PARTIAL"
    assert post["execution_policy_outcome"] == {
        "outcome": "SKIPPED",
        "reason": "software collection not performed; external paper management; completion unverified",
        "origin": "execution_policy",
        "operator_approved": False,
        "external_management": True,
        "real_completion_inferred": False,
        "completion_unverified": True,
        "software_collection_performed": False,
    }
    component_post = controller.session.result["components"]["post"]
    assert component_post["survey"] == "NOT_RECORDED"
    assert component_post["debrief"] == "NOT_RECORDED"
    assert component_post["data_use"] == "NOT_RECORDED"
    assert component_post["analysis_eligible"] is False
    with controller.session.services.logger.event_path.open(newline="") as source:
        event_types = {row["event_type"] for row in csv.DictReader(source)}
    assert event_types.isdisjoint(
        {
            "consent_status",
            "eligibility_status",
            "manual_driving_status",
            "noa_on_off_status",
            "brake_disengage_reactivate_status",
            "exit_confirm_status",
            "exit_reject_status",
            "surt_status",
            "operator_assessment_status",
            "survey_status",
            "debrief_status",
            "data_use_status",
        }
    )
