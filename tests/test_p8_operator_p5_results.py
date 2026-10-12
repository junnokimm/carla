from __future__ import annotations

import csv
import json
from pathlib import Path

from src.experiment.exit_assistance import ExitContext
from src.p8.operator_exit_ledger_view import (
    LiveCoordinatorEvidence,
    LiveExitEvent,
    immutable_event,
)
from src.p8.operator_exit_result import (
    ExitOpportunityClassification,
    configured_exit_result,
)
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_exit_ledger import reconstruct_exit_ledger


def _write_events(
    path: Path, rows: tuple[tuple[str, int, dict[str, str | int | None]], ...]
) -> None:
    fields = ("host_monotonic_ns", "event_type", "event_value")
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for event_type, stamp, payload in rows:
            payload.setdefault("route_id", "route-39")
            payload.setdefault("route_version", "dev-v3")
            payload.setdefault("context", "MODULE")
            payload.setdefault("module", "MODULE_1")
            writer.writerow(
                {
                    "host_monotonic_ns": stamp,
                    "event_type": event_type,
                    "event_value": json.dumps(payload),
                }
            )


def _config(tmp_path: Path) -> ResearchExitAssistanceConfig:
    return ResearchExitAssistanceConfig(
        manifest_path=tmp_path / "routes.json",
        route_id="route-39",
        lc_event_id="run:MODULE_1_RUNNING:route-39",
        navigation_trigger_distance_m=1000.0,
        response_timeout_s=5.0,
        lateral_onset_threshold_m=0.15,
        confirm_key="c",
        reject_key="r",
    )


def _training_config(tmp_path: Path) -> ResearchExitAssistanceConfig:
    return ResearchExitAssistanceConfig(
        manifest_path=tmp_path / "routes.json",
        route_id="route-39",
        lc_event_id="run:TRAINING:route-39",
        context=ExitContext.TRAINING,
        navigation_trigger_distance_m=1000.0,
        response_timeout_s=5.0,
        lateral_onset_threshold_m=0.15,
        confirm_key="c",
        reject_key="r",
    )


def _live(path: Path, status: str = "COMPLETED") -> LiveCoordinatorEvidence:
    events = tuple(
        immutable_event(event)
        for summary in reconstruct_exit_ledger(path)
        for event in summary.events
        if event.event_type.startswith("lc_")
    )
    return LiveCoordinatorEvidence(status, events, status, events)


def test_result_classifies_persisted_completed_exit_without_inventing_times(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            (
                "p5_exit_manifest",
                10,
                {
                    "lc_event_id": event_id,
                    "route_id": "route-39",
                    "route_version": "dev-v3",
                    "context": "MODULE",
                },
            ),
            (
                "lc_presented",
                20,
                {
                    "lc_event_id": event_id,
                    "t0_host_monotonic_ns": 20,
                    "noa_state_t0": "NOA_ACTIVE",
                    "decision": None,
                },
            ),
            (
                "lc_decision",
                30,
                {
                    "lc_event_id": event_id,
                    "t0_host_monotonic_ns": 20,
                    "decision": "CONFIRM",
                },
            ),
            (
                "lc_completed",
                60,
                {
                    "lc_event_id": event_id,
                    "t0_host_monotonic_ns": 20,
                    "outcome": "EXIT",
                    "tL_host_monotonic_ns": None,
                },
            ),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path), live=_live(path))

    assert result.classification is ExitOpportunityClassification.EXIT_CONFIRMED
    assert result.route_version == "dev-v3"
    assert result.decision == "CONFIRM"
    assert result.t0_host_monotonic_ns == 20
    assert result.t_l_host_monotonic_ns is None


def test_result_keeps_reject_as_normal_decision_not_fake_outcome(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            (
                "p5_exit_manifest",
                10,
                {
                    "lc_event_id": event_id,
                    "route_id": "route-39",
                    "route_version": "dev-v3",
                    "context": "MODULE",
                    "module": "MODULE_1",
                },
            ),
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            (
                "lc_decision",
                30,
                {"lc_event_id": event_id, "decision": "REJECT"},
            ),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.DECISION_RECORDED
    assert result.decision == "REJECT"
    assert result.physical_outcome is None


def test_result_marks_configured_but_unobserved_opportunity_explicitly(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    _write_events(path, ())

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.CONFIGURED_NOT_RUN
    assert result.t0_host_monotonic_ns is None


def test_missing_ledger_with_live_coordinator_is_incomplete_not_unexecuted(
    tmp_path: Path,
) -> None:
    from src.p8.operator_exit_ledger_view import LiveCoordinatorEvidence

    path = tmp_path / "events.csv"
    _write_events(path, ())
    live = LiveCoordinatorEvidence("WAITING", (), "WAITING", ())

    result = configured_exit_result(path, _config(tmp_path), live=live)

    assert result.classification is ExitOpportunityClassification.INCOMPLETE
    assert not result.observed_opportunity


def test_result_requires_manifest_and_single_presentation_for_completed_exit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            (
                "lc_completed",
                30,
                {
                    "lc_event_id": event_id,
                    "t0_host_monotonic_ns": 20,
                    "outcome": "EXIT",
                },
            ),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.INCOMPLETE
    assert result.observed_opportunity is False


def test_time_cap_interruption_does_not_imply_participant_interruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            ("p5_exit_manifest", 10, {"lc_event_id": event_id}),
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            (
                "lc_finished",
                30,
                {"lc_event_id": event_id, "termination_reason": "TIME_CAP"},
            ),
        ),
    )

    result = configured_exit_result(
        path,
        _config(tmp_path),
        live=_live(path, "INTERRUPTED"),
    )

    assert result.classification is ExitOpportunityClassification.PRESENTED


def test_blocked_assistance_retains_observed_physical_exit(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            ("p5_exit_manifest", 10, {"lc_event_id": event_id}),
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            ("lc_decision", 30, {"lc_event_id": event_id, "decision": "CONFIRM"}),
            (
                "lc_finished",
                40,
                {
                    "lc_event_id": event_id,
                    "termination_reason": "ASSISTED_EXECUTION_BLOCKED",
                },
            ),
            (
                "lc_completed",
                50,
                {"lc_event_id": event_id, "outcome": "EXIT"},
            ),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.BLOCKED
    assert result.physical_outcome == "EXIT"
    assert result.observed_opportunity is True
    assert result.terminal_event == "lc_completed"


def test_duplicate_blocked_evidence_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            ("p5_exit_manifest", 10, {"lc_event_id": event_id}),
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            ("lc_decision", 30, {"lc_event_id": event_id, "decision": "CONFIRM"}),
            (
                "lc_finished",
                40,
                {
                    "lc_event_id": event_id,
                    "termination_reason": "ASSISTED_EXECUTION_BLOCKED",
                },
            ),
            (
                "lc_finished",
                41,
                {
                    "lc_event_id": event_id,
                    "termination_reason": "ASSISTED_EXECUTION_BLOCKED",
                },
            ),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.INCOMPLETE


def test_presented_event_without_t0_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            ("p5_exit_manifest", 10, {"lc_event_id": event_id}),
            ("lc_presented", 20, {"lc_event_id": event_id}),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.INCOMPLETE


def test_same_type_live_payload_tampering_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            ("p5_exit_manifest", 10, {"lc_event_id": event_id}),
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            (
                "lc_completed",
                30,
                {
                    "lc_event_id": event_id,
                    "t0_host_monotonic_ns": 20,
                    "outcome": "MISSED",
                },
            ),
        ),
    )
    persisted = _live(path)
    completed = persisted.events[-1]
    tampered_payload = tuple(
        (key, "EXIT" if key == "outcome" else value) for key, value in completed.payload
    )
    tampered = LiveCoordinatorEvidence(
        "COMPLETED",
        (*persisted.events[:-1], LiveExitEvent("lc_completed", 30, tampered_payload)),
        "COMPLETED",
        (*persisted.events[:-1], LiveExitEvent("lc_completed", 30, tampered_payload)),
    )

    result = configured_exit_result(path, _config(tmp_path), live=tampered)

    assert result.classification is ExitOpportunityClassification.INCOMPLETE


def test_training_identity_accepts_null_module_payload(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:TRAINING:route-39"
    _write_events(
        path,
        (
            (
                "p5_exit_manifest",
                10,
                {"lc_event_id": event_id, "context": "TRAINING", "module": None},
            ),
            (
                "lc_presented",
                20,
                {
                    "lc_event_id": event_id,
                    "context": "TRAINING",
                    "module": None,
                    "t0_host_monotonic_ns": 20,
                },
            ),
            (
                "lc_decision",
                30,
                {
                    "lc_event_id": event_id,
                    "context": "TRAINING",
                    "module": None,
                    "decision": "REJECT",
                },
            ),
        ),
    )

    result = configured_exit_result(path, _training_config(tmp_path))

    assert result.classification is ExitOpportunityClassification.DECISION_RECORDED


def test_real_operator_identity_requires_full_research_header(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(path, (("p5_exit_manifest", 10, {"lc_event_id": event_id}),))

    result = configured_exit_result(
        path,
        _config(tmp_path),
        participant_id="R-0042",
    )

    assert result.classification is ExitOpportunityClassification.INCOMPLETE


def test_malformed_ledger_is_secondary_to_primary_technical_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    path.write_text("event_value\n{\n", encoding="utf-8")
    primary = RuntimeError("primary run failure")

    result = configured_exit_result(
        path,
        _config(tmp_path),
        technical_error=primary,
    )

    assert result.classification is ExitOpportunityClassification.TECHNICAL_ERROR
    assert result.technical_error == "RuntimeError:primary run failure"
    assert len(result.secondary_errors) == 1
    assert result.secondary_errors[0].startswith("JSONDecodeError:")


def test_result_rejects_duplicate_presentations_and_decision_without_t0(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    manifest = {
        "lc_event_id": event_id,
        "route_id": "route-39",
        "route_version": "dev-v3",
        "context": "MODULE",
        "module": "MODULE_1",
    }
    _write_events(
        path,
        (
            ("p5_exit_manifest", 10, manifest),
            ("lc_presented", 20, {"lc_event_id": event_id}),
            ("lc_presented", 21, {"lc_event_id": event_id}),
            ("lc_decision", 22, {"lc_event_id": event_id, "decision": "REJECT"}),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path))

    assert result.classification is ExitOpportunityClassification.INCOMPLETE
    assert result.observed_opportunity is False


def test_user_exit_after_physical_exit_retains_outcome_but_interrupts_classification(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    event_id = "run:MODULE_1_RUNNING:route-39"
    _write_events(
        path,
        (
            (
                "p5_exit_manifest",
                10,
                {
                    "lc_event_id": event_id,
                    "route_id": "route-39",
                    "route_version": "dev-v3",
                    "context": "MODULE",
                    "module": "MODULE_1",
                },
            ),
            (
                "lc_presented",
                20,
                {"lc_event_id": event_id, "t0_host_monotonic_ns": 20},
            ),
            (
                "lc_completed",
                30,
                {
                    "lc_event_id": event_id,
                    "t0_host_monotonic_ns": 20,
                    "outcome": "EXIT",
                },
            ),
        ),
    )

    result = configured_exit_result(path, _config(tmp_path), user_exited=True)

    assert (
        result.classification is ExitOpportunityClassification.PARTICIPANT_INTERRUPTED
    )
    assert result.physical_outcome == "EXIT"
    assert result.observed_opportunity is False
