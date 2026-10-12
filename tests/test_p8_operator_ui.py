from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pygame
import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import P8Error, Stage
from src.p8.operator_model import (
    DevOutcome,
    DoneClaim,
    ModuleRunResult,
    OperatorSnapshot,
    Receipt,
    RegistrationRequest,
    TrainingRun,
)
from src.p8.operator_pages import stage_page
from src.p8.operator_ui import OperatorApp, OperatorUiConfig
from src.p8.operator_ui_runtime import run_operator_ui


class FakeController:
    def __init__(self, stage: Stage = Stage.SESSION_CREATED) -> None:
        self.stage = stage
        self.registrations: list[RegistrationRequest] = []
        self.abort_reasons: list[str] = []
        self.run_stages: list[Stage] = []
        self.approvals: list[tuple[DevOutcome, str]] = []
        self.receipts: list[Receipt] = []
        self.advances = 0
        self.training_durations: list[float] = []
        self.module_durations: list[float | None] = []

    def register_id(self, request: RegistrationRequest) -> OperatorSnapshot:
        self.registrations.append(request)
        return self.snapshot()

    def snapshot(self) -> OperatorSnapshot:
        return OperatorSnapshot(
            stage=self.stage,
            participant_id="R-0042",
            run_id="operator-run",
            stage_elapsed_s=0.0,
            closed=self.stage in (Stage.SESSION_COMPLETE, Stage.ABORTED),
            allowed_receipts=(),
            receipts={},
            stage_approval=None,
            module_runs=(),
            analysis_eligible=False,
            deletion_status="NOT_REQUESTED",
        )

    def abort(self, reason: str) -> DoneClaim:
        self.abort_reasons.append(reason)
        self.stage = Stage.ABORTED
        return DoneClaim("ABORTED", Stage.ABORTED, False, True, Path("manifest.json"))

    def record_actual(self, receipt: Receipt) -> OperatorSnapshot:
        self.receipts.append(receipt)
        return self.snapshot()

    def advance(self) -> OperatorSnapshot:
        self.advances += 1
        sequence = tuple(item for item in Stage if item is not Stage.ABORTED)
        self.stage = sequence[sequence.index(self.stage) + 1]
        return self.snapshot()

    def execute_training(
        self,
        training: TrainingRun,
        duration: float,
    ) -> ModuleRunResult:
        assert training is TrainingRun.MANUAL_NOA
        self.training_durations.append(duration)
        return self._run(Stage.TRAINING, duration)

    def execute_current_module(self, duration: float | None = None) -> ModuleRunResult:
        planned = 600.0 if self.stage is Stage.MODULE_1_RUNNING else 780.0
        requested = planned if duration is None else duration
        self.module_durations.append(duration)
        return self._run(self.stage, requested)

    def approve_dev_outcome(
        self,
        outcome: DevOutcome,
        reason: str,
    ) -> OperatorSnapshot:
        self.approvals.append((outcome, reason))
        return self.snapshot()

    def record_execution_policy_outcome(
        self,
        outcome: DevOutcome,
        reason: str,
    ) -> OperatorSnapshot:
        self.approvals.append((outcome, reason))
        return self.snapshot()

    def _run(self, stage: Stage, duration: float) -> ModuleRunResult:
        self.run_stages.append(stage)
        return ModuleRunResult(
            stage,
            duration,
            duration,
            "MANUAL",
            0.0,
            0,
            0,
            0,
            None,
            0.0,
            None,
            False,
        )


class UserExitController(FakeController):
    def execute_current_module(self, duration: float | None = None) -> ModuleRunResult:
        self.stage = Stage.ABORTED
        raise P8Error("USER_EXIT_ABORTED_OPERATING_SESSION")


class FakeFrameClock:
    def __init__(self) -> None:
        self.ticks: list[int] = []

    def tick(self, frames_per_second: int) -> int:
        self.ticks.append(frames_per_second)
        return 0


def config(
    duration: float | None = None, training_duration: float = 600.0
) -> OperatorUiConfig:
    return OperatorUiConfig(
        registration=RegistrationRequest(
            participant_id="R-0042",
            run_id="operator-run",
            m1_condition=Module1Condition.NO_SURT,
            m2_condition=Module2Condition.MANUAL,
        ),
        training_duration_s=training_duration,
        drive_duration_s=duration,
    )


def test_startup_auto_registers_and_runs_one_continuous_training() -> None:
    controller = FakeController()
    app = OperatorApp(controller, config(1.0))

    app.start()

    assert len(controller.registrations) == 1
    assert controller.training_durations == [600.0]
    assert controller.stage is Stage.EYE_TRACKER_CALIBRATION
    assert all(
        receipt.status != "OPERATOR_CONFIRMED" for receipt in controller.receipts
    )


def test_default_module_launches_use_stage_nominal_duration() -> None:
    controller = FakeController(stage=Stage.MODULE_1_PREPARE)
    app = OperatorApp(controller, config())
    app.registered = True

    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
    controller.stage = Stage.MODULE_2_PREPARE
    app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])

    assert controller.module_durations == [None, None]


def test_first_space_after_start_records_calibration_boundary() -> None:
    controller = FakeController()
    app = OperatorApp(controller, config(1.0))
    app.start()

    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
    app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])

    assert controller.stage is Stage.MODULE_1_PREPARE


def test_training_return_blocks_held_space_until_release(monkeypatch) -> None:
    pygame.init()
    try:
        monkeypatch.setattr(
            pygame.key,
            "get_pressed",
            lambda: {pygame.K_SPACE: True},
        )
        controller = FakeController()
        app = OperatorApp(controller, config(1.0))

        app.start()
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        assert controller.stage is Stage.EYE_TRACKER_CALIBRATION

        app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        assert controller.stage is Stage.MODULE_1_PREPARE
    finally:
        pygame.quit()


def test_display_restore_replaces_frame_clock_after_training_and_module(
    monkeypatch,
) -> None:
    clocks: list[FakeFrameClock] = []

    def build_clock() -> FakeFrameClock:
        clock = FakeFrameClock()
        clocks.append(clock)
        return clock

    monkeypatch.setattr(pygame.time, "Clock", build_clock)
    pygame.init()
    try:
        controller = FakeController()
        app = OperatorApp(controller, config(1.0))
        initial_clock = app.frame_clock

        app.start()
        training_return_clock = app.frame_clock
        controller.stage = Stage.MODULE_1_PREPARE
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])

        assert clocks == [initial_clock, training_return_clock, app.frame_clock]
        assert len({id(clock) for clock in clocks}) == 3
    finally:
        pygame.quit()


def test_visible_pages_are_only_calibration_instructions_rest_and_final() -> None:
    controller = FakeController(stage=Stage.EYE_TRACKER_CALIBRATION)

    calibration = stage_page(controller.snapshot(), "")
    controller.stage = Stage.MODULE_1_PREPARE
    module_1 = stage_page(controller.snapshot(), "")
    controller.stage = Stage.REST
    rest = stage_page(controller.snapshot(), "")
    controller.stage = Stage.MODULE_2_PREPARE
    module_2 = stage_page(controller.snapshot(), "")
    controller.stage = Stage.SESSION_COMPLETE
    final = stage_page(controller.snapshot(), "")

    assert [page.title for page in (calibration, module_1, rest, module_2, final)] == [
        "Tobii 외부 보정",
        "Module 1 안내",
        "휴식 시간",
        "Module 2 안내",
        "세션 저장 완료",
    ]
    assert rest.body == (
        "Module 1이 종료되었습니다.",
        "약 2분간 휴식을 취한 후 다음 실험을 진행하겠습니다.",
    )
    assert rest.controls == ("SPACE — Module 2 안내로 이동",)
    assert all("120" not in line for line in (*rest.body, *rest.controls))


def test_rest_space_advances_once_and_fresh_second_space_launches_m2() -> None:
    controller = FakeController(stage=Stage.REST)
    app = OperatorApp(controller, config(1.0))
    app.registered = True

    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])

    assert controller.stage is Stage.MODULE_2_PREPARE
    assert controller.advances == 1
    assert controller.run_stages == []

    app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])

    assert controller.run_stages == [Stage.MODULE_2_RUNNING]


def test_final_page_distinguishes_nominal_windows_from_experiment_completion() -> None:
    controller = FakeController(stage=Stage.SESSION_COMPLETE)
    module_1 = replace(
        controller._run(Stage.MODULE_1_RUNNING, 600.0),
        planned_duration_s=600.0,
        nominal_request=True,
    )
    module_2 = replace(
        controller._run(Stage.MODULE_2_RUNNING, 780.0),
        planned_duration_s=780.0,
        nominal_request=True,
    )
    snapshot = replace(controller.snapshot(), module_runs=(module_1, module_2))

    page = stage_page(snapshot, "")

    assert page.body[0] == "600초/780초 명목 주행 구간은 완료되었습니다."
    assert any("PARTIAL" in line for line in page.body)
    assert any("분석 가능 상태를 뜻하지 않습니다" in line for line in page.body)


def test_held_and_repeat_drive_keys_cannot_relaunch_after_viewer_return() -> None:
    pygame.init()
    try:
        controller = FakeController(stage=Stage.MODULE_2_PREPARE)
        app = OperatorApp(controller, config(1.0))
        app.registered = True

        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
        app.handle_events(
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, repeat=1)]
        )

        assert controller.run_stages == [Stage.MODULE_2_RUNNING]
    finally:
        pygame.quit()


def test_runner_return_drops_stale_key_batch_and_releases_drive_key() -> None:
    pygame.init()
    try:
        controller = FakeController(stage=Stage.MODULE_1_PREPARE)
        app = OperatorApp(controller, config(1.0))
        app.registered = True

        app.handle_events(
            [
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),
                pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE),
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),
                pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE),
            ]
        )
        controller.stage = Stage.MODULE_2_PREPARE
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])
        assert controller.run_stages == [Stage.MODULE_1_RUNNING]
        app.handle_events([pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)])
        app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)])

        assert controller.run_stages == [Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING]
    finally:
        pygame.quit()


def test_non_space_key_does_not_launch_module() -> None:
    controller = FakeController(stage=Stage.MODULE_1_RUNNING)
    app = OperatorApp(controller, config(1.0))
    app.registered = True

    app.handle_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d)])

    assert controller.run_stages == []


def test_terminal_runner_error_stops_operator_without_another_key() -> None:
    controller = UserExitController(stage=Stage.MODULE_1_PREPARE)
    app = OperatorApp(controller, config(1.0))
    app.registered = True

    running = app.handle_events(
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)]
    )

    assert running is False
    assert app.status == "USER_EXIT_ABORTED_OPERATING_SESSION"


def test_quit_aborts_and_cleans_the_registered_session() -> None:
    controller = FakeController()
    app = OperatorApp(controller, config())
    app.registered = True

    running = app.handle_events([pygame.event.Event(pygame.QUIT)])

    assert running is False
    assert controller.abort_reasons == ["OPERATOR_WINDOW_QUIT"]


def test_run_operator_ui_returns_nonzero_for_abort_and_quits_pygame(
    monkeypatch,
) -> None:
    controller = FakeController(stage=Stage.EYE_TRACKER_CALIBRATION)
    batches = iter(([pygame.event.Event(pygame.QUIT)],))
    monkeypatch.setattr(pygame.event, "get", lambda: next(batches))

    result = run_operator_ui(controller, config(1.0))

    assert result == 2
    assert pygame.get_init() is False


def test_run_operator_ui_quits_pygame_when_render_fails(monkeypatch) -> None:
    monkeypatch.setattr(pygame.event, "get", list)
    monkeypatch.setattr(
        OperatorApp,
        "render",
        lambda self, surface: (_ for _ in ()).throw(RuntimeError("render failed")),
    )

    with pytest.raises(RuntimeError, match="render failed"):
        run_operator_ui(FakeController(), config(1.0))

    assert pygame.get_init() is False


def test_operator_loop_ticks_the_current_display_generation_clock(monkeypatch) -> None:
    clocks: list[FakeFrameClock] = []

    def build_clock() -> FakeFrameClock:
        clock = FakeFrameClock()
        clocks.append(clock)
        return clock

    batches = iter(
        (
            [
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),
                pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE),
            ],
            [
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),
                pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE),
            ],
            [pygame.event.Event(pygame.QUIT)],
        )
    )
    monkeypatch.setattr(pygame.time, "Clock", build_clock)
    monkeypatch.setattr(pygame.event, "get", lambda: next(batches))

    result = run_operator_ui(FakeController(), config(1.0))

    assert result == 2
    assert len(clocks) == 3
    assert clocks[-1].ticks == [30]


def test_rendered_calibration_screen_contains_nonempty_pixels() -> None:
    pygame.init()
    try:
        surface = pygame.Surface((1280, 720))
        app = OperatorApp(FakeController(stage=Stage.EYE_TRACKER_CALIBRATION), config())
        app.registered = True

        app.render(surface)

        assert pygame.mask.from_surface(surface).count() == 1280 * 720
    finally:
        pygame.quit()
