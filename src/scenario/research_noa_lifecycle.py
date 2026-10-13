from __future__ import annotations

import sys
from collections.abc import Callable

from src.experiment.session_clock import SessionClockError
from src.logging.research_csv_error import ResearchCsvError
from src.scenario.research_noa_persistence import ResearchNoAPersistence
from src.scenario.research_noa_report import ResearchSmokeReport


def retain_first_error(
    action: Callable[[], None], primary_error: BaseException | None
) -> BaseException | None:
    try:
        action()
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        ResearchCsvError,
        SessionClockError,
    ) as error:
        if primary_error is None:
            return error
        note = f"Secondary cleanup failure: {type(error).__name__}:{error}"
        notes = getattr(primary_error, "__notes__", None)
        if notes is None:
            object.__setattr__(primary_error, "__notes__", [])
            notes = primary_error.__notes__
        notes.append(note)
    return primary_error


def preserve_primary_error(
    action: Callable[[], None], primary_error: BaseException | None
) -> None:
    retained_error = retain_first_error(action, primary_error)
    if primary_error is None and retained_error is not None:
        raise retained_error


def run_with_persistence(
    operation: Callable[[], ResearchSmokeReport | None],
    persistence: ResearchNoAPersistence,
) -> ResearchSmokeReport | None:
    persistence.start_stage_attempt()
    result: ResearchSmokeReport | None = None
    try:
        result = operation()
        return result
    finally:
        primary_error = sys.exception()
        if primary_error is not None:
            reason = f"FAILURE:{type(primary_error).__name__}"
        elif result is not None and result.user_exited:
            reason = "USER_EXIT"
        elif result is not None and result.training_completed_early:
            reason = "TRAINING_COMPLETED_EARLY"
        else:
            reason = "NORMAL_COMPLETION"
        if persistence.stage_active:
            preserve_primary_error(lambda: persistence.end_stage(reason), primary_error)
        elif not persistence.stage_ended:
            preserve_primary_error(
                lambda: persistence.fail_stage_start(reason), primary_error
            )
