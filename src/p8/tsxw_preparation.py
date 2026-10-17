from __future__ import annotations

import json
from collections.abc import Callable
from time import perf_counter
from typing import Final

import pygame

from src.error_notes import append_error_note
from src.p8.operator_render import OperatorPage, OperatorRenderer
from src.p8.tsxw_input import TsxwDeviceError, TsxwInput, TsxwLogEvent
from src.p8.tsxw_policy import SDL_AXIS_POSITIVE_ENDPOINT, TsxwInputSample

# Device readiness confirmation only; this does not change pedal deadzones or
# experiment response thresholds. A discontinuous sample restarts confirmation.
RELEASE_CONFIRMATION_SAMPLES: Final = 2
PREPARATION_FRAME_RATE: Final = 30
CANCELLED: Final = "TSXW_INPUT_PREPARATION_CANCELLED"


def pedal_is_released(raw: float) -> bool:
    return raw in (SDL_AXIS_POSITIVE_ENDPOINT, 1.0)


def prepare_tsxw_input(
    adapter: TsxwInput,
    screen: pygame.Surface,
    *,
    hold_brake: Callable[[], None],
    noa_key: int,
    event_sink: Callable[[TsxwLogEvent], None] | None,
) -> None:
    renderer = OperatorRenderer(screen.get_size())
    clock = pygame.time.Clock()
    started_at = perf_counter()
    sample: TsxwInputSample | None = None
    focused = False
    controls_released = False
    sample_count = 0
    released_samples = 0

    def emit(event_type: str, reason: str | None = None) -> None:
        if event_sink is None:
            return
        event_sink(
            TsxwLogEvent(
                event_type,
                json.dumps(
                    {
                        "raw": (
                            None
                            if sample is None
                            else {
                                "steering": sample.raw_steering,
                                "brake": sample.raw_brake,
                                "throttle": sample.raw_throttle,
                            }
                        ),
                        "window_input_focus": focused,
                        "controls_released": controls_released,
                        "sample_count": sample_count,
                        "release_confirmation_samples": RELEASE_CONFIRMATION_SAMPLES,
                        "wait_seconds": perf_counter() - started_at,
                        "reason": reason,
                    },
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )

    emit("tsxw_input_preparation_started")
    try:
        while True:
            events = pygame.event.get()
            if any(
                event.type == pygame.QUIT
                or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
                for event in events
            ):
                raise TsxwDeviceError(CANCELLED)
            hold_brake()
            sample = adapter.poll(events)
            sample_count += 1
            focused = pygame.key.get_focused()
            keys = pygame.key.get_pressed()
            controls_released = not any(
                keys[key]
                for key in (
                    pygame.K_w,
                    pygame.K_s,
                    pygame.K_a,
                    pygame.K_d,
                    pygame.K_SPACE,
                    pygame.K_z,
                    pygame.K_x,
                    pygame.K_r,
                    noa_key,
                )
            ) and not any(
                (
                    sample.left_held,
                    sample.right_held,
                    sample.noa_held,
                    sample.reject_held,
                )
            )
            brake_released = pedal_is_released(sample.raw_brake)
            throttle_released = pedal_is_released(sample.raw_throttle)
            if focused and controls_released and brake_released and throttle_released:
                released_samples += 1
            else:
                released_samples = 0
            if released_samples >= RELEASE_CONFIRMATION_SAMPLES:
                emit("tsxw_input_preparation_ready")
                return
            renderer.draw(
                screen,
                OperatorPage(
                    eyebrow="TS-XW",
                    title="운전 장치 준비",
                    body=(
                        "이 창을 클릭하고 가속·브레이크 페달을 각각 한 번 밟은 뒤 완전히 떼어 주세요.",
                        "키보드 키와 핸들 버튼을 모두 놓아 주세요.",
                        "페달을 뗀 상태가 확인되면 자동으로 주행을 시작해요.",
                        "주행 시간은 장치 준비가 끝난 뒤 시작해요.",
                    ),
                    controls=(
                        f"가속 페달: {'떼기 확인' if throttle_released else '확인 대기'}",
                        f"브레이크 페달: {'떼기 확인' if brake_released else '확인 대기'}",
                        "Esc: 이번 실행 중단",
                    ),
                    status=(
                        "창을 클릭해 주세요."
                        if not focused
                        else (
                            "키보드 키와 핸들 버튼을 놓아 주세요."
                            if not controls_released
                            else "페달 상태를 확인하고 있어요."
                        )
                    ),
                ),
            )
            pygame.display.flip()
            clock.tick(PREPARATION_FRAME_RATE)
    except BaseException as error:
        event_type = (
            "tsxw_input_preparation_cancelled"
            if isinstance(error, TsxwDeviceError) and str(error) == CANCELLED
            else "tsxw_input_preparation_failed"
        )
        try:
            emit(event_type, f"{type(error).__name__}:{error}")
        except Exception as log_error:  # noqa: BLE001 -- keep the original input failure
            append_error_note(error, f"TS-XW preparation log failure: {log_error}")
        raise
