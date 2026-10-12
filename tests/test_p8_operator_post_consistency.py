from __future__ import annotations

import json
from pathlib import Path

from src.p8.model import Stage
from src.p8.operator_model import DevOutcome, Receipt
from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev


def test_accepted_dev_data_use_remains_analysis_ineligible_everywhere(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "M1 bounded")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "M2 bounded")
    controller.advance()
    controller.approve_dev_outcome(DevOutcome.SKIPPED, "hazard blocked")
    controller.advance()
    controller.record_actual(Receipt("survey", "NOT_IMPLEMENTED", None))
    controller.record_actual(Receipt("debrief", "EXTERNAL_OPERATOR_STEP", True))
    controller.record_actual(Receipt("data_use", "OPERATOR_CONFIRMED", True))

    claim = controller.advance()

    snapshot = controller.snapshot()
    component_post = controller._operator_components().snapshot()["post"]
    manifest = json.loads(claim.manifest_path.read_text(encoding="utf-8"))
    assert snapshot.analysis_eligible is False
    assert snapshot.deletion_status == "NOT_REQUESTED"
    assert component_post["analysis_eligible"] is False
    assert component_post["data_use"] == {
        "status": "OPERATOR_CONFIRMED",
        "value": True,
    }
    assert component_post["deletion_status"] == "NOT_REQUESTED"
    assert manifest["analysis_eligible"] is False
    assert manifest["components"]["post"] == component_post
