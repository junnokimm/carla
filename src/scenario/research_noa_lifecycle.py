from __future__ import annotations

import sys
from collections.abc import Callable

from src.experiment.session_clock import SessionClockError
from src.logging.research_csv_error import ResearchCsvError
from src.scenario.research_noa_persistence import ResearchNoAPersistence
from src.scenario.research_noa_report import ResearchSmokeReport


def preserve_primary_error(
    action: Callable[[], None], primary_error: BaseException | None
) -> None:
    try:
        action()
    except (OSError, RuntimeError, ResearchCsvError, SessionClockError):
        if primary_error is None:
            raise


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
        else:
            reason = "NORMAL_COMPLETION"
        if persistence.stage_started:
            preserve_primary_error(lambda: persistence.end_stage(reason), primary_error)
        else:
            preserve_primary_error(
                lambda: persistence.fail_stage_start(reason), primary_error
            )
