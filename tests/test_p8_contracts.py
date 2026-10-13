import ast
from dataclasses import fields, replace
from pathlib import Path

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.assignment import development_assignment
from src.p8.model import Mode, P8Error, Stage, TimingProfile
from src.p8.readiness import Readiness, require_participant_ready


def test_v5_production_durations_are_not_accelerated_defaults():
    production = TimingProfile.production()
    accelerated = TimingProfile.development(Mode.ACCELERATED)

    assert production.minimum(Stage.MODULE_1_RUNNING) == 600
    assert production.minimum(Stage.REST) == 120
    assert production.minimum(Stage.MODULE_2_RUNNING) == 780
    assert accelerated.minimum(Stage.MODULE_1_RUNNING) < 600
    assert accelerated.minimum(Stage.REST) < 120
    assert accelerated.minimum(Stage.MODULE_2_RUNNING) < 780
    assert production.minimum(Stage.MODULE_1_RUNNING) == 600


@pytest.mark.parametrize("m1", list(Module1Condition))
@pytest.mark.parametrize("m2", list(Module2Condition))
def test_development_condition_binding_never_derives_m2_from_m1(m1, m2):
    assignment = development_assignment(m1, m2)

    assert assignment.m1_condition is m1
    assert assignment.m2_condition is m2
    assert assignment.participant_id.startswith("DEV-P8-")


def test_missing_hardware_blocks_production_independently_of_stub_success():
    readiness = Readiness()

    with pytest.raises(P8Error) as caught:
        require_participant_ready(readiness, source_kind="DEV_STUB")

    for blocker in ("tablet_ready", "tablet_aoi_ready", "clock_sync_ready", "p7_live_validated"):
        assert blocker in str(caught.value)


def test_forged_all_true_readiness_cannot_enable_unimplemented_participant_runner():
    readiness = Readiness()
    claimed = replace(readiness, **{field.name: True for field in fields(readiness)})

    with pytest.raises(P8Error, match="PARTICIPANT_RUNTIME_NOT_IMPLEMENTED"):
        require_participant_ready(claimed, source_kind="REAL")


def test_training_stage_list_does_not_include_hazard():
    from src.p8.model import TRAINING_REQUIREMENTS

    assert set(TRAINING_REQUIREMENTS) == {
        "manual_driving", "noa_on_off", "brake_disengage_reactivate",
        "exit_confirm", "exit_reject", "surt", "operator_assessment",
    }


def test_p8_never_adds_an_ego_actuator_or_hardware_acquisition_call():
    forbidden = {"apply_control", "set_autopilot", "set_transform", "find_all_eyetrackers", "subscribe_to"}
    for path in (Path(__file__).resolve().parents[1] / "src/p8").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not calls & forbidden, path.name
