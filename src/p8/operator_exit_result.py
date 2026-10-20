from __future__ import annotations

import csv
from pathlib import Path

from src.p8.operator_exit_identity import exit_identity_conflict
from src.p8.operator_exit_ledger_view import (
    LiveCoordinatorEvidence,
    events_of_type,
    immutable_event,
    latest_text,
)
from src.p8.operator_exit_types import (
    ExitOpportunityClassification,
    ExitOpportunityResult,
)
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_exit_ledger import reconstruct_exit_ledger


def not_configured_exit_result() -> ExitOpportunityResult:
    return ExitOpportunityResult(
        ExitOpportunityClassification.NOT_CONFIGURED,
        configured=False,
    )


def configured_exit_result(
    path: Path,
    config: ResearchExitAssistanceConfig,
    *,
    technical_error: BaseException | None = None,
    live: LiveCoordinatorEvidence | None = None,
    user_exited: bool = False,
    participant_id: str | None = None,
    secondary_errors: tuple[BaseException, ...] = (),
) -> ExitOpportunityResult:
    try:
        summaries = tuple(
            summary
            for summary in reconstruct_exit_ledger(path)
            if summary.lc_event_id == config.lc_event_id
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        csv.Error,
    ) as evidence_error:
        if technical_error is None:
            raise
        return _base_result(
            config,
            ExitOpportunityClassification.TECHNICAL_ERROR,
            technical_error=technical_error,
            live=live,
            secondary_errors=(*secondary_errors, evidence_error),
        )
    if len(summaries) != 1:
        classification = (
            ExitOpportunityClassification.TECHNICAL_ERROR
            if technical_error is not None
            else ExitOpportunityClassification.CONFIGURED_NOT_RUN
            if not summaries and live is None
            else ExitOpportunityClassification.INCOMPLETE
        )
        return _base_result(
            config,
            classification,
            technical_error=technical_error,
            live=live,
            secondary_errors=secondary_errors,
        )
    summary = summaries[0]
    events = summary.events
    manifests = events_of_type(events, "p5_exit_manifest")
    presented_events = events_of_type(events, "lc_presented")
    decision_events = events_of_type(events, "lc_decision")
    completed_events = events_of_type(events, "lc_completed")
    finished_events = events_of_type(events, "lc_finished")
    terminal_events = tuple(
        sorted(
            (*completed_events, *finished_events),
            key=lambda event: event.host_monotonic_ns,
        )
    )
    manifest = None if len(manifests) != 1 else manifests[0]
    termination_reason = latest_text(events, "termination_reason")
    identity_conflict = exit_identity_conflict(
        path,
        config,
        events,
        participant_id,
    )
    duplicate_evidence = any(
        len(group) > 1
        for group in (
            manifests,
            presented_events,
            decision_events,
            completed_events,
            finished_events,
        )
    )
    persisted_events = tuple(
        immutable_event(event) for event in events if event.event_type.startswith("lc_")
    )
    live_conflict = live is not None and (
        live.events != persisted_events
        or persisted_events[: len(live.pre_cleanup_events)] != live.pre_cleanup_events
    )
    if technical_error is not None or termination_reason == "ERROR":
        classification = ExitOpportunityClassification.TECHNICAL_ERROR
    elif user_exited:
        classification = ExitOpportunityClassification.PARTICIPANT_INTERRUPTED
    elif identity_conflict or duplicate_evidence or live_conflict or manifest is None:
        classification = ExitOpportunityClassification.INCOMPLETE
    elif (
        termination_reason == "ASSISTED_EXECUTION_BLOCKED"
        and summary.decision == "CONFIRM"
    ):
        classification = ExitOpportunityClassification.BLOCKED
    elif len(completed_events) == 1 and not finished_events:
        if (
            len(presented_events) != 1
            or summary.t0_host_monotonic_ns is None
            or termination_reason is not None
            or live is None
            or live.status != "COMPLETED"
            or live.pre_cleanup_status != "COMPLETED"
            or live.pre_cleanup_events != live.events
        ):
            classification = ExitOpportunityClassification.INCOMPLETE
        else:
            completed = {
                "EXIT": ExitOpportunityClassification.EXIT_CONFIRMED,
                "MISSED": ExitOpportunityClassification.NORMAL_MISSED,
            }
            classification = completed.get(
                summary.outcome,
                ExitOpportunityClassification.INCOMPLETE,
            )
            if (config.signal_guided_exit and summary.decision == "NORESPONSE"
                    and summary.outcome == "EXIT"):
                classification = ExitOpportunityClassification.INCOMPLETE
    elif termination_reason in (
        "USER_EXIT",
        "MODE_CHANGED_AFTER_T0",
        "DRIVER_PRIORITY_OR_MOVEMENT",
        "MODE_CHANGED_AFTER_AUTO_START",
        "DRIVER_SIGNAL_PRIORITY",
    ) or summary.decision in (
        "UNAVAILABLE_DRIVER_PRIORITY",
        "UNAVAILABLE_MOVEMENT_STARTED",
    ):
        classification = ExitOpportunityClassification.PARTICIPANT_INTERRUPTED
    elif summary.decision is not None:
        classification = (
            ExitOpportunityClassification.DECISION_RECORDED
            if len(presented_events) == 1 and summary.t0_host_monotonic_ns is not None
            else ExitOpportunityClassification.INCOMPLETE
        )
    elif presented_events:
        classification = (
            ExitOpportunityClassification.PRESENTED
            if len(presented_events) == 1 and summary.t0_host_monotonic_ns is not None
            else ExitOpportunityClassification.INCOMPLETE
        )
    else:
        classification = ExitOpportunityClassification.CONFIGURED_NOT_PRESENTED
    return ExitOpportunityResult(
        classification=classification,
        configured=True,
        lc_event_id=config.lc_event_id,
        context=config.context.value,
        manifest_path=str(config.manifest_path),
        route_id=config.route_id,
        route_version=(
            None if manifest is None else latest_text((manifest,), "route_version")
        ),
        coordinator_status=None if live is None else live.status,
        decision=summary.decision,
        physical_outcome=summary.outcome,
        termination_reason=termination_reason,
        noa_state_t0=latest_text(events, "noa_state_t0"),
        t0_host_monotonic_ns=summary.t0_host_monotonic_ns,
        t_l_host_monotonic_ns=summary.tL_host_monotonic_ns,
        terminal_event=None if not terminal_events else terminal_events[-1].event_type,
        technical_error=(
            None
            if technical_error is None
            else f"{type(technical_error).__name__}:{technical_error}"
        ),
        secondary_errors=tuple(
            f"{type(error).__name__}:{error}" for error in secondary_errors
        ),
    )


def _base_result(
    config: ResearchExitAssistanceConfig,
    classification: ExitOpportunityClassification,
    *,
    technical_error: BaseException | None,
    live: LiveCoordinatorEvidence | None = None,
    secondary_errors: tuple[BaseException, ...] = (),
) -> ExitOpportunityResult:
    return ExitOpportunityResult(
        classification=classification,
        configured=True,
        lc_event_id=config.lc_event_id,
        context=config.context.value,
        manifest_path=str(config.manifest_path),
        route_id=config.route_id,
        coordinator_status=None if live is None else live.status,
        technical_error=(
            None
            if technical_error is None
            else f"{type(technical_error).__name__}:{technical_error}"
        ),
        secondary_errors=tuple(
            f"{type(error).__name__}:{error}" for error in secondary_errors
        ),
    )


def technical_exit_result(
    config: ResearchExitAssistanceConfig,
    error: BaseException,
    live: LiveCoordinatorEvidence | None,
    secondary_errors: tuple[BaseException, ...] = (),
) -> ExitOpportunityResult:
    return _base_result(
        config,
        ExitOpportunityClassification.TECHNICAL_ERROR,
        technical_error=error,
        live=live,
        secondary_errors=secondary_errors,
    )
