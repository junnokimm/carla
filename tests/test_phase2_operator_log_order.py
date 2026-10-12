from __future__ import annotations

import csv
import json
from pathlib import Path

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance import ExitContext
from src.experiment.lane_change import LaneChangeDirection
from src.p8.model import Stage
from src.p8.operator_model import TrainingRun
from tests.test_p8_operator_backend import (
    StrictClock,
    advance_to,
    exit_template,
    register_dev,
)


def test_active_lane_request_normal_stop_preserves_shared_clock(tmp_path: Path) -> None:
    controller, evidence = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.TRAINING)
    evidence.driver_inputs.append(
        DriverInput(
            activation_requested=True,
            lane_change_direction=LaneChangeDirection.RIGHT,
        )
    )
    try:
        result = controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)

        assert result.user_exited is False
        with controller.session.services.logger.event_path.open(
            newline="", encoding="utf-8"
        ) as stream:
            rows = list(csv.DictReader(stream))
        timestamps = [int(row["host_monotonic_ns"]) for row in rows]
        assert timestamps == sorted(timestamps)
        ends = [
            json.loads(row["event_value"])
            for row in rows
            if row["event_type"] == "lane_change_observation_end"
        ]
        assert len(ends) == 1
        assert ends[0]["reason"] == "NORMAL_STOP"
        assert ends[0]["physical_completion_claimed"] is False
        assert evidence.vehicles[-1].destroy_count == 1
    finally:
        if not controller.session.closed:
            controller.abort("TEST_TEARDOWN")


def test_training_exit_does_not_inherit_module_timing(tmp_path: Path) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
        training_exit=exit_template(ExitContext.TRAINING),
    )
    advance_to(controller, Stage.TRAINING)
    try:
        controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)

        binding = evidence.viewers[-1].exit_view_binding
        assert binding is not None
        assert binding.config.eligibility_open_s is None
        assert binding.config.eligibility_close_s is None
    finally:
        if not controller.session.closed:
            controller.abort("TEST_TEARDOWN")
