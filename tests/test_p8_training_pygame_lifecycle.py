from __future__ import annotations

import csv
import time
from pathlib import Path

import pygame
import pytest

from src.p8.model import Stage
from src.p8.operator_ui import OperatorApp
from src.p8.operator_ui_runtime import run_operator_ui
from tests.p8_training_pygame_lifecycle_support import (
    LifecycleEvidence,
    build_controller,
    prepare_sdl,
    ui_config,
)


def test_f8_training_restores_operator_display_and_accepts_space_then_quit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence = LifecycleEvidence()
    controller = build_controller(
        tmp_path,
        evidence,
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8),
    )
    prepare_sdl(monkeypatch)

    class EventPostingOperatorApp(OperatorApp):
        def _restore_display(self, *, require_space_release: bool) -> None:
            super()._restore_display(require_space_release=require_space_release)
            evidence.restored_at = time.perf_counter()
            evidence.restored_video_driver = pygame.display.get_driver()
            evidence.restored_surface = pygame.display.get_surface() is not None
            evidence.calls.append("operator.display_restored")
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
            pygame.event.post(pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE))

        def render(self, surface: pygame.Surface) -> None:
            super().render(surface)
            if "operator.quit_posted" not in evidence.calls:
                evidence.calls.append("operator.quit_posted")
                pygame.event.post(pygame.event.Event(pygame.QUIT))

        def _abort(self, reason: str) -> None:
            evidence.quit_handled_at = time.perf_counter()
            evidence.calls.append(f"operator.abort:{reason}")
            super()._abort(reason)

    monkeypatch.setattr(
        "src.p8.operator_ui_runtime.OperatorApp",
        EventPostingOperatorApp,
    )
    started_at = time.perf_counter()

    exit_code = run_operator_ui(controller, ui_config())

    elapsed = time.perf_counter() - started_at
    snapshot = controller.snapshot()
    assert exit_code == 2
    assert snapshot.stage is Stage.ABORTED
    assert snapshot.module_runs[0].training_completed_early is True
    assert snapshot.module_runs[0].completion_reason == "OPERATOR_EARLY_FINISH"
    assert evidence.configured_durations == [600.0]
    assert evidence.restored_surface is True
    assert evidence.restored_video_driver != "dummy"
    assert evidence.restored_at is not None
    assert evidence.quit_handled_at is not None
    assert evidence.quit_handled_at - evidence.restored_at < 0.5
    assert elapsed < 2.0
    assert evidence.borrowed_logger_ids == [id(controller.session.services.logger)]
    assert evidence.borrowed_clock_ids == [id(controller.session.services.clock)]
    assert evidence.calls == [
        "sensor.listen",
        f"driver.event:{pygame.KEYDOWN}",
        "viewer.close",
        "sensor.stop",
        "sensor.destroy",
        "hero.destroy",
        "operator.display_restored",
        "operator.quit_posted",
        "operator.abort:OPERATOR_WINDOW_QUIT",
    ]
    assert evidence.viewer_close_counts == [1]
    assert evidence.sensors[0].stop_count == 1
    assert evidence.sensors[0].destroy_count == 1
    assert evidence.heroes[0].destroy_count == 1
    assert controller.session.cleanup_report["completed"] is True
    records = {record["stage"]: record for record in controller.session.records}
    consent = records[Stage.CONSENT_AND_ELIGIBILITY.value]
    training = records[Stage.TRAINING.value]
    assert consent["receipts"] == {}
    assert training["receipts"] == {}
    assert consent["execution_policy_outcome"] == {
        "outcome": "SKIPPED",
        "reason": "software collection not performed; external paper management; completion unverified",
        "origin": "execution_policy",
        "operator_approved": False,
        "external_management": True,
        "real_completion_inferred": False,
        "completion_unverified": True,
        "software_collection_performed": False,
    }
    training_policy = training["execution_policy_outcome"]
    assert training_policy["outcome"] == "PARTIAL"
    assert training_policy["operator_approved"] is False
    assert training_policy["external_management"] is True
    assert training_policy["real_completion_inferred"] is False
    assert training_policy["completion_unverified"] is True
    assert training_policy["skill_assessment"] == "EXTERNAL_UNVERIFIED"
    assert training_policy["p5_surt_connected"] is False
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
        }
    )
    assert pygame.get_init() is False
    print(
        f"pygame_lifecycle elapsed={elapsed:.3f}s "
        f"restore_to_quit={evidence.quit_handled_at - evidence.restored_at:.3f}s "
        f"video_driver={evidence.restored_video_driver} "
        f"calls={evidence.calls}"
    )


@pytest.mark.parametrize(
    "event",
    [
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE),
        pygame.event.Event(pygame.QUIT),
    ],
    ids=["escape", "quit"],
)
def test_training_abort_events_exit_and_clean_actual_driver_view(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    event: pygame.event.Event,
) -> None:
    evidence = LifecycleEvidence()
    controller = build_controller(tmp_path, evidence, event)
    prepare_sdl(monkeypatch)

    assert run_operator_ui(controller, ui_config()) == 2

    result = controller.snapshot().module_runs[0]
    assert result.user_exited is True
    assert result.training_completed_early is False
    assert evidence.configured_durations == [600.0]
    assert evidence.viewer_close_counts == [1]
    assert evidence.sensors[0].stop_count == 1
    assert evidence.sensors[0].destroy_count == 1
    assert evidence.heroes[0].destroy_count == 1
    assert pygame.get_init() is False
