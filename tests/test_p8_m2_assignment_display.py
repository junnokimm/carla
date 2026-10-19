"""Assignment text reflects session context, not live NoA state or a redraw."""
from dataclasses import replace

import pytest

from src.experiment.context import Module2Condition
from src.p8.model import Stage
from src.p8.operator_copy import MODULE_2_INSTRUCTION
from src.p8.operator_pages import stage_page
from tests.test_p8_operator_ui import FakeController


@pytest.mark.parametrize(
    ("condition", "title"),
    [
        (Module2Condition.MANUAL, "Module 2 안내 — 배정: 수동 (MANUAL)"),
        (Module2Condition.NOA_L2, "Module 2 안내 — 배정: NoA형 (NOA_L2)"),
    ],
)
def test_prepare_title_shows_assignment_and_preserves_common_instructions(
    condition, title
):
    snapshot = replace(
        FakeController(Stage.MODULE_2_PREPARE).snapshot(), module_2_condition=condition
    )
    page = stage_page(snapshot, "")
    assert page.title == title
    assert page.body == MODULE_2_INSTRUCTION
    assert page.controls == ("SPACE  안내 완료 후 Module 2 시작",)
    for hidden in ("위험", "장애물", "화물", "400", "600", "TTC"):
        assert hidden not in " ".join((page.title, *page.body, *page.controls))


def test_unknown_assignment_is_not_silently_shown_as_manual():
    snapshot = FakeController(Stage.MODULE_2_PREPARE).snapshot()
    assert stage_page(snapshot, "").title == "Module 2 안내"


def test_m1_page_is_unchanged_by_m2_assignment():
    base = FakeController(Stage.MODULE_1_PREPARE).snapshot()
    manual = stage_page(replace(base, module_2_condition=Module2Condition.MANUAL), "")
    noa = stage_page(replace(base, module_2_condition=Module2Condition.NOA_L2), "")
    assert manual == noa
