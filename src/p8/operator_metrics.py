from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from src.experiment.context import ExperimentPhase
from src.p8.model import P8Error, Stage
from src.p8.operator_model import ModuleRunResult


@dataclass(frozen=True, slots=True)
class PersistedEvent:
    phase: str
    event_type: str
    event_value: str
    session_s: float


def event_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as source:
        return sum(1 for _ in csv.DictReader(source))


def reduce_module_run(
    path: Path,
    *,
    offset: int,
    stage: Stage,
    requested_duration_s: float,
    actual_active_drive_s: float,
    user_exited: bool,
    planned_duration_s: float,
    runtime_started_session_s: float,
    runtime_closed_session_s: float,
    completion_reason: str,
    training_completed_early: bool,
    nominal_request: bool,
    fallback_rows: list | None = None,
) -> ModuleRunResult:
    if fallback_rows is not None:
        rows = fallback_rows
    else:
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))[offset:]
    expected_phase = {
        Stage.MODULE_1_RUNNING: ExperimentPhase.MODULE_1.value,
        Stage.MODULE_2_RUNNING: ExperimentPhase.MODULE_2.value,
    }.get(stage)
    events = sorted(
        (
            PersistedEvent(
                row["phase"],
                row["event_type"],
                row["event_value"],
                float(row["session_elapsed_s"]),
            )
            for row in rows
            if row["session_elapsed_s"]
            and (expected_phase is None or row["phase"] == expected_phase)
        ),
        key=lambda event: event.session_s,
    )
    initial = next(
        (event for event in events if event.event_type == "initial_state"), None
    )
    start = next((event for event in events if event.event_type == "stage_start"), None)
    end = next(
        (event for event in reversed(events) if event.event_type == "stage_end"), None
    )
    if initial is None or start is None or end is None:
        raise P8Error("PERSISTED_MODULE_WINDOW_INCOMPLETE")
    initial_mode = initial.event_value.rsplit("|", 1)[-1]
    active = initial_mode == "NOA_ACTIVE"
    cursor = start.session_s
    active_seconds = 0.0
    nominal_active_seconds = 0.0
    nominal_end_s = start.session_s + planned_duration_s
    activation_times = [
        event.session_s
        for event in events
        if event.event_type == "auto_state"
        and event.event_value.endswith("NOA_ACTIVE")
        and event.session_s <= end.session_s
    ]
    deactivations = 0
    for event in events:
        if event.session_s < start.session_s or event.session_s > end.session_s:
            continue
        if event.event_type == "auto_state":
            if active:
                active_seconds += max(0.0, event.session_s - cursor)
                nominal_active_seconds += max(
                    0.0, min(event.session_s, nominal_end_s) - cursor
                )
            next_active = event.event_value.endswith("NOA_ACTIVE")
            if active and not next_active:
                deactivations += 1
            active = next_active
            cursor = event.session_s
    if active:
        active_seconds += max(0.0, end.session_s - cursor)
        nominal_active_seconds += max(0.0, min(end.session_s, nominal_end_s) - cursor)
    observed_window = max(0.0, end.session_s - start.session_s)
    first_latency = (
        None
        if not activation_times
        else max(0.0, activation_times[0] - start.session_s)
    )
    return ModuleRunResult(
        stage=stage,
        requested_duration_s=requested_duration_s,
        actual_active_drive_s=actual_active_drive_s,
        initial_mode=initial_mode,
        automation_on_seconds=active_seconds,
        activation_count=len(activation_times),
        deactivation_count=deactivations,
        reactivation_count=max(0, len(activation_times) - 1),
        first_activation_latency_s=first_latency,
        observed_on_proportion=(
            None if observed_window <= 0.0 else active_seconds / observed_window
        ),
        nominal_on_proportion=(
            nominal_active_seconds / planned_duration_s
            if nominal_request
            and stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING)
            and actual_active_drive_s >= planned_duration_s
            and not user_exited
            and completion_reason == "TIME_LIMIT_REACHED"
            else None
        ),
        user_exited=user_exited,
        planned_duration_s=planned_duration_s,
        actual_active_start_session_s=start.session_s,
        actual_active_end_session_s=end.session_s,
        runtime_started_session_s=runtime_started_session_s,
        runtime_closed_session_s=runtime_closed_session_s,
        completion_reason=completion_reason,
        training_completed_early=training_completed_early,
        nominal_request=nominal_request,
    )
