"""Explicit end-of-drive boundary for contact with the owned M2 obstacle."""

import copy

import carla
import pygame

from src.experiment.automation import DrivingControlMode
from src.p8.operator_render import OperatorPage, OperatorRenderer

COLLISION_END = "HAZARD_COLLISION"


class HazardCollisionStop:
    def __init__(self, scene, collisions):
        self.scene = scene
        self.session = scene.session
        self.active_seconds = scene.elapsed
        self.sample = self.session.persistence.source.last_snapshot_sample
        self.record = {
            "reason": COLLISION_END,
            "status": "COLLISION_DETECTED",
            "m2_active_elapsed_s": self.active_seconds,
            "auto_state_at_collision": self.session.bundle.automation_runtime.state.control_mode.value,
            "collisions": copy.deepcopy(collisions),
            "wait_seconds": 0.0,
            "confirmation": None,
            "errors": [],
            "wait_policy": "EXCLUDE_FROM_ACTIVE_DRIVE",
        }

    def _attempt(self, step, action):
        try:
            action()
            return True
        except Exception as error:  # noqa: BLE001 -- keep the notice and collision evidence usable
            self.record["errors"].append(
                {"step": step, "error": f"{type(error).__name__}:{error}"}
            )
            return False

    def _emit(self, name):
        self._attempt(name, lambda: self.scene.emit(name, copy.deepcopy(self.record)))

    def wait(self):
        """Called once by the viewer; no ordinary input/control iteration runs here."""
        if self.record["confirmation"] is not None:
            return
        self.session.viewer.collision_stop = self
        started = self.session.monotonic_clock()
        self.record["wait_started_monotonic_s"] = started
        self.record["status"] = "AWAITING_CONFIRMATION"
        # Close the analytic interval before the terminal stop changes control mode.
        self._attempt("flush_drive_events", self.session.persistence.flush_events)
        self._attempt(
            "end_drive_interval",
            lambda: self.session.persistence.end_stage(COLLISION_END),
        )
        self._attempt(
            "freeze_view_timing",
            self.session.viewer.freeze_performance_measurement_window,
        )
        self._attempt(
            "freeze_control_timing",
            self.session.live_scheduler.diagnostics.freeze_measurement_window,
        )
        # This existing shutdown path unregisters the ego even if already Manual.
        # An unregistered ego is not eligible for TM's blocked-vehicle destruction.
        self.record["tm_release_requested"] = self._attempt(
            "release_traffic_manager",
            lambda: self.session.request_control_mode(DrivingControlMode.MANUAL),
        )
        self.record["brake_requested"] = self._attempt(
            "hold_brake",
            lambda: (self.session.control_sender or self.session.hero.apply_control)(
                carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True)
            ),
        )
        # Stop the collision listener now, so indefinite waiting cannot grow its
        # callback queue. Owned actors (including the ego) remain until confirmation.
        if self.scene.backend._sensor is not None:
            self._attempt("stop_collision_sensor", self.scene.backend._sensor.stop)
        self._attempt("flush_collision_samples", self.scene._flush_raw)
        self._emit("collision_stop_wait")
        self.record["confirmation"] = self._wait_for_confirmation()
        self.record["confirmed_monotonic_s"] = self.session.monotonic_clock()
        self.record["wait_seconds"] = self.record["confirmed_monotonic_s"] - started
        self.record["status"] = "CONFIRMED"
        self._emit("collision_stop_confirmed")

    def _wait_for_confirmation(self):
        screen = pygame.display.get_surface()
        renderer = OperatorRenderer(screen.get_size())
        clock = pygame.time.Clock()
        # A Space pressed before the notice (or an OS key repeat) cannot confirm.
        pygame.event.clear((pygame.KEYDOWN, pygame.KEYUP))
        awaiting_release = bool(pygame.key.get_pressed()[pygame.K_SPACE])
        page = OperatorPage(
            "실험 종료 안내",
            "장애물과 충돌하여 실험을 종료합니다",
            ("스페이스 바를 누르면 데이터 수집을 종료하고 결과를 저장합니다.",),
            ("SPACE  데이터 저장 및 실험 종료",),
            "관리자 확인 필요: 정지 또는 기록 요청 오류 있음"
            if self.record["errors"]
            else "종료 확인 대기",
        )
        while True:
            renderer.draw(screen, page)
            pygame.display.flip()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return "WINDOW_CLOSE"
                if event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                    awaiting_release = False
                if event.type != pygame.KEYDOWN or event.dict.get("repeat", 0):
                    continue
                if event.key == pygame.K_ESCAPE:
                    return "OPERATOR_ESCAPE"
                if event.key == pygame.K_SPACE and not awaiting_release:
                    return "SPACE"
            clock.tick(20)
