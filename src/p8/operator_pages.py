from __future__ import annotations

from typing import assert_never

from src.experiment.context import Module2Condition
from src.p8.model import P8Error, Stage
from src.p8.operator_copy import MODULE_1_INSTRUCTION, MODULE_2_INSTRUCTION
from src.p8.operator_model import OperatorSnapshot
from src.p8.operator_render import OperatorPage


def stage_page(snapshot: OperatorSnapshot, status: str) -> OperatorPage:
    header = (
        f"{snapshot.participant_id}  /  {snapshot.run_id}  /  {snapshot.stage.value}"
    )
    match snapshot.stage:
        case Stage.EYE_TRACKER_CALIBRATION:
            title = "Tobii 외부 보정"
            body = (
                "Tobii 외부 보정 절차를 완료해 주세요.",
                "이 프로그램은 보정 성공이나 AOI 확인을 추정하지 않습니다.",
            )
            controls = ("SPACE  외부 절차 경계 기록 후 Module 1 안내",)
        case Stage.MODULE_1_PREPARE:
            title = "Module 1 안내"
            body = MODULE_1_INSTRUCTION
            controls = ("SPACE  안내 완료 후 Module 1 시작",)
        case Stage.REST:
            title = "휴식 시간"
            body = (
                "Module 1이 종료되었습니다.",
                "약 2분간 휴식을 취한 후 다음 실험을 진행하겠습니다.",
            )
            controls = ("SPACE — Module 2 안내로 이동",)
        case Stage.MODULE_2_PREPARE:
            match snapshot.module_2_condition:
                case Module2Condition.MANUAL:
                    title = "Module 2 안내 — 배정: 수동 (MANUAL)"
                case Module2Condition.NOA_L2:
                    title = "Module 2 안내 — 배정: NoA형 (NOA_L2)"
                case _:
                    title = "Module 2 안내"
            body = MODULE_2_INSTRUCTION
            controls = ("SPACE  안내 완료 후 Module 2 시작",)
        case Stage.SESSION_COMPLETE:
            title = (
                "원자료 저장 · ZIP 내보내기 실패"
                if snapshot.result_export is not None
                and snapshot.result_export.error is not None
                else "세션 저장 완료"
            )
            module_runs = tuple(
                run
                for run in snapshot.module_runs
                if run.stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING)
            )
            nominal_complete = len(module_runs) == 2 and all(
                run.window_complete for run in module_runs
            )
            exit_summary = ", ".join(
                (
                    "NOT_CONFIGURED"
                    if run.exit_opportunity is None
                    else run.exit_opportunity.classification.value
                )
                for run in module_runs
            )
            body = (
                (
                    "600초/780초 명목 주행 구간은 완료되었습니다."
                    if nominal_complete
                    else "실행된 부분 주행 구간이 저장되었습니다."
                ),
                f"P5 단계별 결과: {exit_summary or '실행 기록 없음'}.",
                f"위험사건 기록: {snapshot.hazard_status or '실행 기록 없음'}.",
                "P6 시선 수집과 위험사건 물리·데이터 검증은 미완료이며 전체 결과는 PARTIAL입니다.",
                "참가자 실험 완료나 분석 가능 상태를 뜻하지 않습니다.",
                *_result_paths(snapshot),
            )
            controls = ("창을 닫아 종료",)
        case Stage.ABORTED:
            title = "세션 중단"
            body = ("부분 기록과 중단 사유를 저장했습니다.", *_result_paths(snapshot))
            controls = ("창을 닫아 종료",)
        case (
            Stage.SESSION_CREATED
            | Stage.CONSENT_AND_ELIGIBILITY
            | Stage.TRAINING
            | Stage.MODULE_1_RUNNING
            | Stage.MODULE_1_COMPLETE
            | Stage.MODULE_2_RUNNING
            | Stage.FINAL_HAZARD
            | Stage.POST_EXPERIMENT
        ):
            raise P8Error("INTERNAL_OPERATIONAL_STAGE_HAS_NO_SCREEN")
        case unreachable:
            assert_never(unreachable)
    return OperatorPage(header, title, body, controls, status)


def _result_paths(snapshot: OperatorSnapshot) -> tuple[str, ...]:
    result = snapshot.result_export
    if result is None:
        return ()
    lines = [f"원자료 폴더: {result.directory}"]
    if result.archive is not None:
        lines.append(f"결과 ZIP: {result.archive}")
    if result.error is not None:
        lines.append(f"ZIP 내보내기 실패: {result.error}")
    return tuple(lines)
