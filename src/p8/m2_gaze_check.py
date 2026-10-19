"""Operator-controlled M2 gaze check. No CARLA client or wheel device is opened."""
# ruff: noqa: BLE001 -- optional gaze errors stay on a visible decision screen
from __future__ import annotations

import json
import queue
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pygame

from src.p6.gaze_precheck import (
    AOIS,
    DEFAULT_POLICY,
    evaluate_attempt,
    evaluate_segment,
)
from src.p6.tobii.client import TobiiProcessSource
from src.p8.model import Stage
from src.p8.operator_render import OperatorPage, OperatorRenderer
from src.p8.pilot_gaze import PilotGaze, notice
from src.scenario.driver_view import DriverViewConfig
from src.scenario.driver_view_presentation import DriverViewPresentation

LABELS = {"FORWARD": "전방 중앙", "LEFT_MIRROR": "좌측 미러 중앙",
          "RIGHT_MIRROR": "우측 미러 중앙", "TABLET": "실제 태블릿의 과제 표시 영역 중앙"}
TARGETS = (*AOIS, "FORWARD")


class CheckCapture(PilotGaze):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observations = queue.Queue(maxsize=18000)
        self.observation_drops = 0

    def _record(self, handle, record):
        super()._record(handle, record)
        if record.get("record_type") == "gaze":
            try:
                self.observations.put_nowait(record)
            except queue.Full:
                self.observation_drops += 1

    def drain(self):
        rows = []
        while True:
            try:
                rows.append(self.observations.get_nowait())
            except queue.Empty:
                return rows


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def record_decision(services, result, *, continued_with_exclusions):
    decision = {**result, "status": "CONTINUE_WITH_EXCLUSIONS" if continued_with_exclusions else "PASSED",
                "operator_action": "EXPLICIT_CONTINUE", "recorded_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": str(services.context.study_run_id),
                "participant_id": str(services.context.participant_id),
                "stage": "MODULE_2_RUNNING", "raw_gaze_preserved": True,
                "missing_gaze_is_not_zero_tablet_time": True}
    services.m2_gaze_quality = decision
    try:
        save_json(services.directory / "m2_gaze_quality.json", decision)
        services.emit("m2_gaze_precheck_decision", decision)
    except Exception as error:
        # Keep the decision in session memory for all subsequent M2 gaze summaries.
        decision["decision_storage_error"] = str(error)
        notice("GAZE_PRECHECK_DECISION_STORAGE_FAILED", reason=str(error))
    return decision


class M2GazeCheck:
    def __init__(self, services, config_path, view_config, *, policy=DEFAULT_POLICY,
                 capture_factory=CheckCapture):
        self.services, self.config_path, self.view_config = services, Path(config_path), view_config
        self.policy, self.capture_factory = policy, capture_factory
        if not hasattr(services, "m2_gaze_attempts"):
            services.m2_gaze_attempts = []
        self.attempts = services.m2_gaze_attempts
        self.result = self.attempts[-1] if self.attempts else evaluate_attempt([])
        self.mode = "RESULT" if self.attempts else "INTRO"
        self.capture = None
        self.presentation = None
        self.target_index = 0
        self.rows, self.segments = [], []
        self.attempt_dir = None
        self.started_at = 0
        self.capture_start = self.capture_end = 0
        self.status = ""
        self.held = {pygame.K_SPACE}  # The key that opened this screen must be released.

    def begin_attempt(self):
        if len(self.attempts) >= self.policy.max_attempts:
            return
        self.target_index = 0
        self.rows, self.segments = [], []
        self.capture = None
        self.started_at = time.perf_counter_ns()
        self.attempt_dir = self.services.directory / "gaze_precheck" / (
            f"M2-{len(self.attempts) + 1}-{uuid4().hex[:8]}")
        self.mode = "CONNECTING"
        try:
            self.attempt_dir.mkdir(parents=True, exist_ok=False)
            isolated = SimpleNamespace(directory=self.attempt_dir, context=self.services.context,
                                       clock=self.services.clock,
                                       pilot_gaze_monitor=dict(getattr(self.services, "pilot_gaze_monitor", {})))
            source = lambda python, **kwargs: TobiiProcessSource(python, seconds=180, pilot=True)
            self.capture = self.capture_factory(isolated, Stage.MODULE_2_PREPARE, self.view_config,
                                                self.config_path, source_factory=source)
            self.capture.start()
        except Exception as error:
            self.finish_attempt([f"START_FAILED:{error}"])

    def finish_attempt(self, errors=()):
        errors = list(errors)
        warnings = []
        if self.capture is not None:
            try:
                self.capture.close()
                warnings = list(self.capture.errors)
                errors.extend(e for e in warnings if not e.startswith("TABLET_PROFILE:"))
                if self.capture.thread is not None and self.capture.thread.is_alive():
                    errors.append("PRECHECK_CAPTURE_SHUTDOWN_PENDING")
                if self.capture.observation_drops:
                    errors.append("PRECHECK_OBSERVATION_QUEUE_DROPS")
            except Exception as error:
                errors.append(f"CLOSE_FAILED:{error}")
        self.result = evaluate_attempt(self.segments, errors, self.policy)
        self.result.update(attempt=len(self.attempts) + 1, warnings=warnings,
                           directory=str(self.attempt_dir),
                           calibration_confirmation="OPERATOR_CONFIRMED_EXISTING_PERSONAL_CALIBRATION_NOT_SDK_VERIFIED",
                           tablet_profile_sha256=(self.capture.tablet.sha256
                                                  if self.capture and self.capture.tablet else None))
        self.attempts.append(self.result)
        try:
            save_json(self.attempt_dir / "check_summary.json", self.result)
        except Exception as error:
            self.result.update(evaluate_attempt(self.segments, [f"SUMMARY_STORAGE_FAILED:{error}"], self.policy))
        self.capture = None
        self.mode = "RESULT"

    def tick(self, now_ns):
        if self.capture is None:
            return
        incoming = self.capture.drain()
        if self.mode == "MEASURING":
            self.rows.extend(incoming)
        if self.mode == "CONNECTING":
            if incoming:
                self.mode = "TARGET"
            elif now_ns - self.started_at > 15_000_000_000:
                self.finish_attempt(["NO_GAZE_START_TIMEOUT"])
        if self.capture is None:
            return
        if self.capture.thread is not None and not self.capture.thread.is_alive():
            self.finish_attempt(["GAZE_CAPTURE_STOPPED"])
            return
        if self.mode == "MEASURING" and now_ns >= self.capture_end + 150_000_000:
            target = TARGETS[self.target_index]
            self.segments.append(evaluate_segment(target, self.rows, self.capture_start,
                                                   self.capture_end, self.policy))
            self.rows = []
            self.target_index += 1
            if self.target_index == len(TARGETS):
                self.finish_attempt()
            else:
                self.mode = "TARGET"

    def handle_key(self, key, now_ns):
        if key == pygame.K_SPACE and self.mode in ("INTRO", "TARGET") and (
                self.presentation is not None and not self.presentation._fullscreen):
            self.status = "F11로 주행 모니터 전체화면으로 전환한 후 진행하세요."
            return None
        if key == pygame.K_ESCAPE:
            if self.capture:
                self.finish_attempt(["OPERATOR_CANCELLED_CHECK"])
            return False  # Return to M2 instructions; never abort the experiment.
        if key == pygame.K_F8 and self.mode in ("INTRO", "RESULT"):
            self.mode = "CONFIRM_EXCLUSIONS"
        elif key == pygame.K_RETURN and self.mode == "CONFIRM_EXCLUSIONS":
            record_decision(self.services, self.result, continued_with_exclusions=True)
            return True
        elif key == pygame.K_BACKSPACE and self.mode == "CONFIRM_EXCLUSIONS":
            self.mode = "RESULT" if self.attempts else "INTRO"
        elif key == pygame.K_r and self.mode == "RESULT":
            self.begin_attempt()
        elif key == pygame.K_SPACE:
            if self.mode == "INTRO":
                self.begin_attempt()
            elif self.mode == "RESULT" and self.result["passed"]:
                record_decision(self.services, self.result, continued_with_exclusions=False)
                return True
            elif self.mode == "TARGET":
                self.rows = []
                self.capture_start = now_ns + int(self.policy.settle_seconds * 1e9)
                self.capture_end = self.capture_start + int(self.policy.seconds * 1e9)
                self.mode = "MEASURING"
        return None

    def draw(self, canvas, renderer, now_ns):
        if self.mode in ("TARGET", "MEASURING"):
            target = TARGETS[self.target_index]
            canvas.fill((12, 20, 24))
            font = pygame.font.SysFont("malgungothic", 26)
            label = LABELS[target]
            canvas.blit(font.render(label + "을 바라보세요", True, (240, 240, 240)), (32, 30))
            if self.mode == "TARGET":
                message = "진행자 SPACE: 1초 준비 후 5초 측정 | F11: 전체화면 | ESC: 안내로 복귀"
            else:
                remaining = max(0, (self.capture_end - now_ns) / 1e9)
                message = f"시선을 유지하세요 · {remaining:.1f}초"
            canvas.blit(pygame.font.SysFont("malgungothic", 18).render(self.status or message, True, (240, 240, 240)), (32, 80))
            if target != "TABLET":
                layout = self.capture.layout
                if target == "FORWARD":
                    point = (canvas.get_width() // 2, canvas.get_height() // 2)
                else:
                    rect = layout.left if target == "LEFT_MIRROR" else layout.right
                    pygame.draw.rect(canvas, (100, 120, 130), (rect.x, rect.y, rect.width, rect.height), 2)
                    point = (round(rect.x + rect.width / 2), round(rect.y + rect.height / 2))
                pygame.draw.circle(canvas, (255, 220, 70), point, 10)
            return
        if self.mode == "INTRO":
            title = "M2 시작 전 시선 영역 확인"
            body = ("현재 피험자의 Tobii 개인 보정을 먼저 완료해 주세요.",
                    "전방 → 좌측 미러 → 우측 미러 → 실제 태블릿 중앙 → 전방을 확인합니다.",
                    "각 지점 1초 준비 + 5초 측정. 평소 주행 자세와 자연스러운 눈 움직임을 유지하세요.",
                    "주행 모니터의 전체화면인지 확인하세요. F11로 창 이동 후 다시 전체화면으로 전환할 수 있습니다.",
                    "이 검사는 개인 보정을 대체하지 않으며, 태블릿 중앙 과제 영역만 확인합니다.")
            controls = ("SPACE  개인 보정 완료·화면 확인 후 검사 시작", "F8  시선 지표 제외하고 진행 확인", "ESC  M2 안내로 복귀")
        elif self.mode == "CONNECTING":
            title, body, controls = "아이트래커 연결 중", ("장비와 현재 매핑을 확인하고 있습니다.",), ("ESC  M2 안내로 복귀",)
        elif self.mode == "CONFIRM_EXCLUSIONS":
            title = "진행자 확인 · 시선 지표 제외"
            excluded = ", ".join(LABELS[a] for a in self.result["excluded_aois"]) or "없음"
            body = (f"M2 시선 분석에서 제외: {excluded}",
                    "주행·핸들·위험사건 기록은 정상 진행합니다.",
                    "원본 시선과 검사 실패 이유는 보존합니다. 추적 손실은 '안 봄'으로 처리하지 않습니다.")
            controls = ("ENTER  위 내용을 확인하고 M2 시작", "BACKSPACE  검사 결과로 돌아가기", "ESC  M2 안내로 복귀")
        else:
            title = "시선 확인 완료" if self.result["passed"] else "시선 확인 · 일부 영역 미충족"
            lines = []
            for a in AOIS:
                rows = [s for s in self.result["segments"] if s["target"] == a]
                rates = (f"  추적 {min(s['tracking_rate'] for s in rows):.0%} / "
                         f"해당 영역 {min(s['target_rate'] for s in rows):.0%}") if rows else "  미측정"
                lines.append(f"{LABELS[a]}: {'합격' if self.result['eligible_aois'][a] else '미충족'}{rates}")
            body = tuple(lines)
            body += (f"시도 {len(self.attempts)}/{self.policy.max_attempts}. 기준은 이번 실험용 잠정 운영 기준입니다.",)
            body += (("재보정이 필요하면 Alt+Tab으로 Eye Tracker Manager에서 보정한 후 돌아와 R을 누르세요.",)
                     if len(self.attempts) < self.policy.max_attempts else
                     ("재검사 횟수가 끝났습니다. 불합격 영역을 제외하고 계속하려면 F8을 누르세요.",))
            if self.result["errors"]:
                body += ("수집/저장 상태: " + str(self.result["errors"][0])[:100],)
            controls = (("SPACE  M2 시작",) if self.result["passed"] else ())
            if len(self.attempts) < self.policy.max_attempts:
                controls += ("R  전체 영역 재검사",)
            controls += ("F8  불합격 영역의 시선 지표 제외하고 진행 확인", "ESC  M2 안내로 복귀")
        renderer.draw(canvas, OperatorPage("M2 아이트래커 확인", title, body, controls, self.status))

    def run(self):
        screen = pygame.display.get_surface() or pygame.display.set_mode(self.view_config.window_size)
        self.presentation = DriverViewPresentation(self.view_config.window_size, screen)
        # pygame retains a borrowed SDL Window pointer. Keep the wrapper alive until
        # the operator's final pygame.quit(), including after this check returns.
        if not hasattr(self.services, "m2_gaze_presentations"):
            self.services.m2_gaze_presentations = []
        self.services.m2_gaze_presentations.append(self.presentation)
        renderer = OperatorRenderer(self.view_config.window_size)
        clock = pygame.time.Clock()
        try:
            self.presentation._toggle_fullscreen()
            while True:
                now = time.perf_counter_ns()
                events = pygame.event.get()
                # Resizing/focus loss during a target makes that attempt invalid.
                if self.mode == "MEASURING" and any(e.type == pygame.WINDOWFOCUSLOST or (
                        e.type == pygame.KEYDOWN and e.key == pygame.K_F11) for e in events):
                    self.finish_attempt(["PRESENTATION_CHANGED_DURING_MEASUREMENT"])
                self.presentation.handle_events(events)
                if self.presentation._fullscreen:
                    self.status = ""
                for event in events:
                    if event.type == pygame.QUIT:
                        return self.handle_key(pygame.K_ESCAPE, now)
                    if event.type == pygame.KEYUP:
                        self.held.discard(event.key)
                    elif event.type == pygame.KEYDOWN and event.key not in self.held and not event.dict.get("repeat", 0):
                        self.held.add(event.key)
                        result = self.handle_key(event.key, now)
                        if result is not None:
                            return result
                self.tick(now)
                self.presentation.gaze_observer = self.capture.present if self.capture else None
                self.draw(self.presentation.canvas, renderer, now)
                self.presentation.present()
                pygame.display.flip()
                clock.tick(30)
        finally:
            self.presentation.gaze_observer = None
            if self.capture:
                self.finish_attempt(["CHECK_UI_CLOSED"])
            if self.presentation._fullscreen:
                self.presentation._toggle_fullscreen()


def prepare_m2_gaze(controller):
    config = getattr(getattr(controller, "runtime", None), "gaze_config", None)
    if config is None:
        return True  # Explicit --no-gaze and legacy non-gaze operator integrations.
    try:
        settings = json.loads(Path(config).read_text(encoding="utf-8-sig"))
        if settings.get("enabled") is False:
            return True
    except (OSError, ValueError, AttributeError):
        pass  # A broken configuration requires the visible failure/decision flow.
    runtime = controller.runtime
    services = controller.session.services
    view = DriverViewConfig(rear_mirror_enabled=runtime.rear_mirror_enabled)
    check = M2GazeCheck(services, config, view)
    try:
        return check.run()
    except Exception as error:
        # Even an optional UI/probe failure requires an explicit decision, not session abort.
        result = evaluate_attempt([], [f"PRECHECK_UNAVAILABLE:{error}"])
        renderer = OperatorRenderer(view.window_size)
        screen = pygame.display.set_mode(view.window_size)
        clock = pygame.time.Clock()
        confirmed = False
        held = set()
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    return False
                if event.type == pygame.KEYUP:
                    held.discard(event.key)
                elif event.type == pygame.KEYDOWN and event.key not in held and not event.dict.get("repeat", 0):
                    held.add(event.key)
                    if event.key == pygame.K_F8:
                        confirmed = True
                    elif event.key == pygame.K_RETURN and confirmed:
                        record_decision(services, result, continued_with_exclusions=True)
                        return True
            renderer.draw(screen, OperatorPage("M2 아이트래커 확인", "시선 검사 실행 불가 · 진행자 확인",
                ("주행 실험은 종료되지 않았습니다. M2의 시선 지표는 모두 제외할 수 있습니다.",
                 "F8을 누른 뒤 Enter로 확인하면 원본 수집을 유지하면서 주행을 시작합니다.", str(error)[:120]),
                ("F8 → ENTER  M2 시선 지표 제외·계속", "ESC  안내로 돌아가 재시도"),
                "ENTER로 계속 진행을 확인하세요" if confirmed else ""))
            pygame.display.flip()
            clock.tick(30)
