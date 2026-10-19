import csv
from pathlib import Path

import pytest

from src.experiment.exit_assistance import ExitContext
from src.p8.model import Stage
from tests.test_p8_operator_backend import (
    StrictClock,
    advance_to,
    exit_template,
    register_dev,
)


@pytest.mark.parametrize("run_failed", [True, False])
def test_live_evidence_failure_cannot_mask_run_error_or_disappear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_failed: bool,
) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    if run_failed:
        evidence.failure = RuntimeError("primary runner failure")

    def fail_evidence(*_args):
        raise ValueError("live evidence failure")

    monkeypatch.setattr(
        "src.p8.operator_execution.after_cleanup_evidence", fail_evidence
    )
    expected_type = RuntimeError if run_failed else ValueError
    expected_message = (
        "primary runner failure" if run_failed else "live evidence failure"
    )

    with pytest.raises(expected_type, match=expected_message):
        controller.execute_current_module()

    assert not controller.session.closed
    assert controller._operator_components().runtime_faults
    assert len(controller.snapshot().module_runs) == 1
    exit_result = controller._operator_components().exit_slots[Stage.MODULE_1_RUNNING]
    assert exit_result.classification.value == "TECHNICAL_ERROR"
    assert expected_message in exit_result.technical_error
    assert evidence.vehicles[-1].destroy_count == 1
    assert evidence.viewers[-1].close_count == 1
    controller.abort("TEST_OPERATOR_STOP")


def test_activation_with_steering_after_manual_t0_keeps_shared_clock_order(
    tmp_path: Path,
) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    evidence.p5_scenario = "off_activation_steer"
    try:
        result = controller.execute_current_module()

        assert not controller.session.closed
        assert result.window_complete
        assert result.exit_opportunity.classification.value == "PARTICIPANT_INTERRUPTED"
        with controller.session.services.logger.event_path.open(
            newline="", encoding="utf-8"
        ) as source:
            rows = list(csv.DictReader(source))
        event_types = {row["event_type"] for row in rows}
        assert "activation_speed_hold" not in event_types
        assert "control_authority" in event_types
        times = [int(row["host_monotonic_ns"]) for row in rows]
        assert times == sorted(times)
    finally:
        controller.abort("TEST_TEARDOWN")
