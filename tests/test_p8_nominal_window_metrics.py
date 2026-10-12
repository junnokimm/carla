import csv
from pathlib import Path

import pytest

from src.p8.model import Stage
from src.p8.operator_metrics import reduce_module_run


@pytest.mark.parametrize(
    ("actual_seconds", "user_exited", "expected_ratio"),
    [(600.0, False, 1 / 600), (599.9, False, None), (600.0, True, None)],
)
def test_nominal_noa_ratio_excludes_on_time_after_nominal_window(
    tmp_path: Path,
    actual_seconds: float,
    user_exited: bool,
    expected_ratio: float | None,
) -> None:
    path = tmp_path / "events.csv"
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(("event_type", "event_value", "session_elapsed_s"))
        writer.writerows(
            (
                ("initial_state", "AVAILABLE|MANUAL", 0),
                ("stage_start", "MODULE_1_RUNNING", 0),
                ("auto_state", "AVAILABLE|NOA_ACTIVE", 599),
                ("auto_state", "AVAILABLE|MANUAL", 610),
                ("stage_end", "MODULE_1_RUNNING", 611),
            )
        )

    result = reduce_module_run(
        path,
        offset=0,
        stage=Stage.MODULE_1_RUNNING,
        requested_duration_s=600,
        actual_active_drive_s=actual_seconds,
        user_exited=user_exited,
        planned_duration_s=600,
        runtime_started_session_s=0,
        runtime_closed_session_s=612,
        completion_reason="TIME_LIMIT_REACHED",
        training_completed_early=False,
        nominal_request=True,
    )

    assert result.window_complete is (expected_ratio is not None)
    assert result.automation_on_seconds == 11
    if expected_ratio is None:
        assert result.nominal_on_proportion is None
    else:
        assert result.nominal_on_proportion == pytest.approx(expected_ratio)
