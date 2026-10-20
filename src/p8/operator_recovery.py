"""Visible operator recovery for runtime errors; never fabricate a healthy drive."""

import time
from contextlib import contextmanager

import pygame

from src.experiment.automation import DrivingControlMode
from src.experiment.observation_clock import observation_ns
from src.p8.operator_render import OperatorPage, OperatorRenderer
from src.p8.tsxw_input import TsxwDeviceError


class OperatorRecovery:
    def __init__(self, session, services, records, *, clock=time.monotonic):
        self.session, self.services, self.records = session, services, records
        self.clock = clock
        self.paused_seconds = 0.0
        self.exit_requested = False

    @contextmanager
    def guard(self, scene):
        try:
            yield
        except Exception as error:  # noqa: BLE001 -- boundary keeps the operator window alive
            self.handle(error, scene)

    def handle(self, error, scene):
        started = self.clock()
        record = {
            "error": f"{type(error).__name__}:{error}",
            "status": "AWAITING_OPERATOR",
            "host_started_s": started,
            "started_perf_counter_ns": observation_ns(),
            "analysis_eligible": False,
            "gap_policy": "EXCLUDE_FROM_ACTIVE_DURATION_KEEP_HOST_TIMESTAMPS",
            "stop_request_errors": [],
        }
        self.records.append(record)
        if scene is not None and hasattr(scene, "invalidate"):
            scene.invalidate(error, "DRIVING_RUNTIME")
        # A returned request is not proof of physical stopping, particularly
        # when the server is unavailable. Preserve the outcome explicitly.
        for action in (
            lambda: self.session.request_control_mode(DrivingControlMode.MANUAL),
        ):
            try:
                action()
            except Exception as failure:  # noqa: BLE001 -- the recovery screen must remain usable
                record["stop_request_errors"].append(str(failure))
        self._emit("operator_runtime_fault", record)
        while True:
            retry = self._wait_for_operator(record)
            if not retry:
                self.exit_requested = True
                record["status"] = "OPERATOR_REQUESTED_STOP"
                break
            try:
                self.session.request_control_mode(DrivingControlMode.MANUAL)
                if isinstance(error, TsxwDeviceError):
                    self.session.viewer.recover_input()
                # Check actual actor availability and storage before resuming.
                self.session.persistence.source.get_observation()
                self.session.persistence.flush_events()
                self.services.emit("operator_runtime_retry", {"error": record["error"]})
            except Exception as failure:  # noqa: BLE001 -- another failure returns to the notice
                record["retry_error"] = f"{type(failure).__name__}:{failure}"
                continue
            record["status"] = "RETRY_AUTHORIZED"
            break
        paused = self.clock() - started
        self.paused_seconds += paused
        record["paused_seconds"] = paused
        record["completed_perf_counter_ns"] = observation_ns()
        if scene is not None:
            scene.started_at += paused
        self._emit("operator_runtime_recovery", record)

    def _emit(self, kind, record):
        try:
            self.services.emit(kind, dict(record))
        except Exception as failure:  # noqa: BLE001 -- retain evidence in the session manifest
            record["recording_error"] = f"{type(failure).__name__}:{failure}"

    def _wait_for_operator(self, record):
        pygame.display.init()
        screen = pygame.display.get_surface() or pygame.display.set_mode((1280, 720))
        renderer = OperatorRenderer(screen.get_size())
        clock = pygame.time.Clock()
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return False
                if event.type == pygame.KEYDOWN and not event.dict.get("repeat", 0):
                    if event.key == pygame.K_ESCAPE:
                        return False
                    if event.key == pygame.K_r:
                        return True
            renderer.draw(
                screen,
                OperatorPage(
                    "실험 진행자 확인",
                    "실험이 일시 중단되었습니다",
                    (
                        "장치·통신·저장 상태를 확인해 주세요. 창과 세션을 유지하고 있습니다.",
                        "이 중단 구간은 정상 주행 데이터로 취급하지 않습니다.",
                        record["error"][:240],
                        record.get("retry_error", "")[:240],
                    ),
                    ("R  복구 확인 후 재개 시도", "ESC / 창 닫기  진행자가 실험 종료"),
                    "정지 명령 전달 오류 있음"
                    if record["stop_request_errors"]
                    else "정지 명령 요청됨 · 실제 정지는 별도 확인",
                ),
            )
            pygame.display.flip()
            clock.tick(20)
