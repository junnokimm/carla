from __future__ import annotations

from typing import assert_never

import pygame

from src.p8.model import P8Error, Stage
from src.p8.operator_model import (
    DevOutcome,
    DoneClaim,
    OperatorSnapshot,
    Receipt,
    TrainingRun,
)
from src.p8.operator_pages import stage_page
from src.p8.operator_render import OperatorPage, OperatorRenderer
from src.p8.operator_ui_types import OperatorControl, OperatorUiConfig
from src.p8.warning_sound_practice import WarningSoundPractice


class OperatorApp:
    def __init__(self, controller: OperatorControl, config: OperatorUiConfig) -> None:
        self.controller = controller
        self.config = config
        self.registered = False
        self.running = True
        self.keys_down: set[int] = set()
        self.discard_key_batch = False
        self.awaiting_space_release = False
        self.status = "시작 대기"
        self.runtime_error = None
        self.warning_practice = None
        self.renderer = OperatorRenderer(config.window_size)
        self.frame_clock = pygame.time.Clock()

    def start(self) -> None:
        self.controller.register_id(self.config.registration)
        self.registered = True
        if self.config.registration.m2_only_test:
            self.status = "M2 단독 테스트 · 훈련·M1·휴식 미실시"
            if getattr(getattr(self.controller, "runtime", None), "m2_hazard_v1", None) is not None:
                self.warning_practice = WarningSoundPractice(self.controller, m2_only_test=True)
            return
        self.controller.advance()
        self.controller.record_execution_policy_outcome(
            DevOutcome.SKIPPED,
            "software collection not performed; external paper management; completion unverified",
        )
        self.controller.advance()
        if getattr(getattr(self.controller, "runtime", None), "m2_hazard_v1", None) is not None:
            self.warning_practice = WarningSoundPractice(self.controller)
            return
        self._run_training()

    def _run_training(self) -> None:
        result = self.controller.execute_training(
            TrainingRun.MANUAL_NOA,
            self.config.training_duration_s,
        )
        self._restore_display(require_space_release=False)
        exit_status = (
            "NOT_CONFIGURED"
            if result.exit_opportunity is None
            else result.exit_opportunity.classification.value
        )
        self.controller.record_execution_policy_outcome(
            DevOutcome.PARTIAL,
            (
                f"actual run captured: requested={result.requested_duration_s:.1f}s, "
                f"active={result.actual_active_drive_s:.1f}s; "
                f"P5={exit_status}; skill assessment and SuRT external/unverified"
            ),
        )
        self.controller.advance()
        self.status = "외부 보정 절차 대기"

    def handle_events(self, events: list[pygame.event.Event]) -> bool:
        for event in events:
            if event.type == pygame.QUIT:
                self._abort("OPERATOR_WINDOW_QUIT")
                return False
            if self.discard_key_batch and event.type in (pygame.KEYDOWN, pygame.KEYUP):
                continue
            if event.type == pygame.KEYUP:
                self.keys_down.discard(event.key)
                if event.key == pygame.K_SPACE:
                    self.awaiting_space_release = False
                continue
            if (
                event.type != pygame.KEYDOWN
                or event.key in self.keys_down
                or event.dict.get("repeat", 0)
                or (event.key == pygame.K_SPACE and self.awaiting_space_release)
            ):
                continue
            self.keys_down.add(event.key)
            if event.key == pygame.K_ESCAPE:
                self._abort("OPERATOR_ESCAPE_ABORT")
                return False
            if self.runtime_error is not None:
                continue
            try:
                self._handle_key(event.key)
            except P8Error as error:
                reason = str(error)
                self.status = reason
                snapshot = self.controller.snapshot()
                if snapshot.stage is Stage.ABORTED:
                    self.running = False
                    return False
        self.discard_key_batch = False
        return self.running

    def render(self, surface: pygame.Surface) -> None:
        if self.runtime_error is not None:
            self.renderer.draw(surface, OperatorPage(
                "실험 진행자 확인", "실행 상태를 확인해 주세요",
                (
                    "현재 주행을 자동 재개할 수 없어 확인을 기다리고 있습니다.",
                    "세션을 임의로 정상 완료하거나 새 주행으로 바꾸지 않습니다.",
                    self.runtime_error[:300],
                ),
                ("ESC / 창 닫기  진행자가 중단하고 결과 저장 시도",),
                "오류 발생 · 관리자 확인 대기",
            ))
            return
        if self.warning_practice is not None:
            self.renderer.draw(surface, self.warning_practice.page())
            self.warning_practice.drawn = True
        else:
            self.renderer.draw(surface, stage_page(self.controller.snapshot(), self.status))

    def after_display_flip(self) -> None:
        if self.warning_practice is not None and self.runtime_error is None:
            self.warning_practice.after_display_flip()

    def close_warning_practice(self, reason="OPERATOR_UI_CLOSED") -> None:
        if self.warning_practice is not None:
            self.warning_practice.close(reason)

    def hold_runtime_error(self, error):
        if self.registered and self.controller.snapshot().closed:
            self.running = False
            return
        self.runtime_error = f"{type(error).__name__}:{error}"
        self._restore_display(require_space_release=True)
        self.running = True

    def _handle_key(self, key: int) -> None:
        if self.warning_practice is not None:
            if key == pygame.K_r:
                self.warning_practice.play("R_REPLAY")
            elif key == pygame.K_SPACE and self.warning_practice.acknowledge():
                self.warning_practice = None
                if not self.config.registration.m2_only_test:
                    self._run_training()
                # Do not forward the acknowledgement or a queued repeat into
                # the post-training stage when the drive returns.
                self.discard_key_batch = True
                self.awaiting_space_release = True
            return
        if key != pygame.K_SPACE:
            return
        stage = self.controller.snapshot().stage
        match stage:
            case Stage.EYE_TRACKER_CALIBRATION:
                self.controller.record_actual(
                    Receipt("calibration_boundary", "EXTERNAL_OPERATOR_STEP", None)
                )
                self._advance()
            case Stage.MODULE_1_PREPARE:
                from src.p8.m2_gaze_check import prepare_module_gaze

                ready = prepare_module_gaze(self.controller, module="M1")
                if getattr(getattr(self.controller, "runtime", None), "gaze_config", None) is not None:
                    self._restore_display(require_space_release=True)
                if ready:
                    self._run_module(stage)
            case Stage.MODULE_2_PREPARE:
                from src.p8.m2_gaze_check import prepare_m2_gaze

                ready = prepare_m2_gaze(self.controller)
                if getattr(getattr(self.controller, "runtime", None), "gaze_config", None) is not None:
                    self._restore_display(require_space_release=True)
                if ready:
                    self._run_module(stage)
            case Stage.REST:
                self._advance()
            case Stage.SESSION_COMPLETE | Stage.ABORTED:
                return
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
                raise P8Error("INTERNAL_OPERATIONAL_STAGE_RECEIVED_INPUT")
            case unreachable:
                assert_never(unreachable)

    def _run_module(self, prepare_stage: Stage) -> None:
        self._advance()
        running_stage = self.controller.snapshot().stage
        result = self.controller.execute_current_module(self.config.drive_duration_s)
        if result.completion_reason == "HAZARD_COLLISION":
            self.status = "장애물 충돌 · 결과 저장 후 종료"
            self.running = False
            return
        self._restore_display(require_space_release=True)
        module = "M1" if running_stage is Stage.MODULE_1_RUNNING else "M2"
        window_status = (
            "nominal drive window completed"
            if result.window_complete
            else "bounded partial drive window"
        )
        exit_status = (
            "NOT_CONFIGURED"
            if result.exit_opportunity is None
            else result.exit_opportunity.classification.value
        )
        m2_internal_result = (
            running_stage is Stage.MODULE_2_RUNNING
            and self.controller.snapshot().stage is Stage.MODULE_2_RUNNING
            and result.nominal_request
        )
        if (
            running_stage is not Stage.MODULE_2_RUNNING
            or not result.nominal_request
            or m2_internal_result
        ):
            self.controller.record_execution_policy_outcome(
                DevOutcome.PARTIAL,
                f"{module} {window_status}; P5={exit_status}; P6 remains blocked",
            )
            self._advance()
        if prepare_stage is Stage.MODULE_1_PREPARE:
            self._advance()
            completion = (
                "600초 명목 구간 완료" if result.window_complete else "부분 구간 저장"
            )
            self.status = f"M1 actual {result.actual_active_drive_s:.1f}s; {completion}"
            return
        if not result.nominal_request or m2_internal_result:
            self.controller.record_execution_policy_outcome(
                DevOutcome.BLOCKED,
                "M2 internal v1 scene recorded; physical/data validation pending; no post-M2 replay"
                if m2_internal_result
                else "moving hazard policy and physical readiness blocked; not run",
            )
        self._advance()
        self.controller.record_execution_policy_outcome(
            DevOutcome.SKIPPED,
            "software collection not performed; external paper management; completion unverified",
        )
        self._advance()
        completion = "780초 명목 구간 완료" if result.window_complete else "부분 구간"
        self.status = (
            f"M2 actual {result.actual_active_drive_s:.1f}s; {completion}; "
            "미연결 요구사항과 함께 저장"
        )

    def _advance(self) -> None:
        result = self.controller.advance()
        match result:
            case DoneClaim(completion_status=status):
                self.status = status
            case OperatorSnapshot(stage=stage):
                self.status = f"진입: {stage.value}"
            case unreachable:
                assert_never(unreachable)

    def _abort(self, reason: str) -> None:
        self.close_warning_practice(reason)
        if self.registered and not self.controller.snapshot().closed:
            self.controller.abort(reason)
        self.running = False

    def _restore_display(self, *, require_space_release: bool) -> None:
        pygame.init()
        pygame.display.set_mode(self.config.window_size)
        self.renderer = OperatorRenderer(self.config.window_size)
        self.frame_clock = pygame.time.Clock()
        self.keys_down.clear()
        pygame.event.clear((pygame.KEYDOWN, pygame.KEYUP))
        self.discard_key_batch = require_space_release
        self.awaiting_space_release = require_space_release or bool(
            pygame.key.get_pressed()[pygame.K_SPACE]
        )
