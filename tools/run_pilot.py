"""Run the existing P8 operator with the recorded local pilot settings."""

from __future__ import annotations

import argparse
import json
import os
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

from src.experiment.context import Module1Condition, Module2Condition
from src.p8 import operator_cli
from src.p8.crash_recovery import register_supervised_run
from src.p8.pilot_identity import (
    reserve_pilot_run,
    select_participant,
    select_session_type,
)
from src.p8.tsxw_policy import PILOT_MANUAL_STEERING_CENTER_AXIS
from src.scenario.research_exit_config import ExitEligibilityWindow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_CONFIG = Path("config/p8_pilot.json")
M1_CHOICES = {"1": "SURT", "2": "NO_SURT"}
M2_CHOICES = {"1": "MANUAL", "2": "NOA_L2"}
DEFAULT_EXIT_GUIDANCE_DURATION_S = 15.0


def select_m1_assignment(value: str | None) -> str:
    if value is not None:
        return M1_CHOICES.get(value, value)
    while True:
        choice = input("M1 배정을 선택하세요 (1=SuRT 시행, 2=SuRT 미시행/NO_SURT): ").strip()
        if choice in M1_CHOICES:
            return M1_CHOICES[choice]
        print("1 또는 2를 입력하세요. 배정 선택 전에는 실험이 시작되지 않습니다.")


def select_m2_assignment(value: str | None) -> str:
    if value is not None:
        return M2_CHOICES.get(value, value)
    while True:
        choice = input("M2 배정을 선택하세요 (1=MANUAL, 2=NoA): ").strip()
        if choice in M2_CHOICES:
            return M2_CHOICES[choice]
        print("1 또는 2를 입력하세요. 배정 선택 전에는 실험이 시작되지 않습니다.")


def load_pilot_settings(path: Path) -> dict:
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    Module1Condition(settings["m1"])
    Module2Condition(settings["m2"])
    guidance_duration = settings.get(
        "exit_guidance_duration_s", DEFAULT_EXIT_GUIDANCE_DURATION_S
    )
    if isinstance(guidance_duration, bool) or not isinstance(guidance_duration, (int, float)):
        raise TypeError("진출 안내 표시 시간은 숫자여야 해.")
    if not isfinite(guidance_duration) or guidance_duration <= 0.0:
        raise ValueError("진출 안내 표시 시간은 유한한 양수여야 해.")
    missing = [
        key for key in ("m2_exit_open_s", "m2_exit_close_s") if settings[key] is None
    ]
    if missing:
        print(
            "M2 진출 안내 시간창이 최신 실행 로그에 남아 있지 않아.\n"
            "기존 설정값을 한 번 입력하면 다음 실행부터 저장된 값을 사용해.\n"
            "단위는 M2 시작 후 초야. 0 <= open < close <= 780 이어야 해."
        )
        for key in missing:
            label = "open" if key.endswith("open_s") else "close"
            settings[key] = float(input(f"M2 P5 {label} 초: "))
    for module, duration in (("m1", 600.0), ("m2", 780.0)):
        opening = settings[f"{module}_exit_open_s"]
        closing = settings[f"{module}_exit_close_s"]
        if isinstance(opening, bool) or isinstance(closing, bool):
            raise TypeError("P5 시간창은 숫자여야 해.")
        window = ExitEligibilityWindow(float(opening), float(closing))
        if window.open_s >= window.close_s:
            raise ValueError(f"{module.upper()} P5 open은 close보다 작아야 해.")
        if window.close_s > duration:
            raise ValueError(f"{module.upper()} P5 close는 {duration:g}초 이하여야 해.")
    if missing:
        with TemporaryDirectory(prefix=".p8-pilot-", dir=path.parent) as directory:
            staged = Path(directory) / path.name
            staged.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            staged.replace(path)
    return settings


def build_operator_args(
    settings: dict, *, participant_id: str, run_id: str
) -> list[str]:
    return [
        "--mode",
        "dev-rehearsal",
        "--ack-dev-live",
        "--participant-id",
        participant_id,
        "--run-id",
        run_id,
        "--m1",
        settings["m1"],
        "--m2",
        settings["m2"],
        "--spawn-index",
        "344",
        "--traffic-manager-port",
        "8000",
        "--tsxw",
        "--tsxw-index",
        "0",
        "--tsxw-steering-deadzone",
        "0.02",
        # Reduce manual play without lowering the NoA steering takeover threshold.
        "--tsxw-manual-steering-deadzone",
        "0.005",
        "--tsxw-manual-steering-center-axis",
        str(PILOT_MANUAL_STEERING_CENTER_AXIS),
        "--exit-route-manifest",
        "config/town04_exit_routes_dev_v3.json",
        "--exit-route-id",
        "town04-exit-47",
        "--training-exit-route-manifest",
        "config/town04_exit_routes_dev_v3.json",
        "--training-exit-route-id",
        "town04-exit-47",
        "--exit-navigation-trigger-distance-m",
        "1000",
        "--exit-response-timeout-s",
        str(settings.get("exit_guidance_duration_s", DEFAULT_EXIT_GUIDANCE_DURATION_S)),
        "--exit-lateral-onset-threshold-m",
        "0.15",
        "--m1-exit-open-s",
        str(settings["m1_exit_open_s"]),
        "--m1-exit-close-s",
        str(settings["m1_exit_close_s"]),
        "--m2-exit-open-s",
        str(settings["m2_exit_open_s"]),
        "--m2-exit-close-s",
        str(settings["m2_exit_close_s"]),
        "--training-duration",
        "600",
        "--m2-hazard-v1",
        "--output-root",
        "data/p8_operator",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PILOT_CONFIG)
    parser.add_argument("--participant-id", "--participant-number", help="참가자 번호; 생략하면 터미널에서 입력")
    parser.add_argument("--session-type", choices=["1", "2", "test", "pilot"],
                        help="1=test, 2=pilot; 생략하면 터미널에서 선택")
    parser.add_argument(
        "--m1",
        choices=[*M1_CHOICES, *[item.value for item in Module1Condition]],
        help="1=SuRT 시행, 2=SuRT 미시행/NO_SURT; 생략하면 터미널에서 선택",
    )
    parser.add_argument("--gaze-config", type=Path, default=Path("config/p8_gaze.json"))
    parser.add_argument("--no-gaze", action="store_true", help="시선 수집을 명시적으로 끄고 주행")
    parser.add_argument(
        "--m2",
        choices=[*M2_CHOICES, *[item.value for item in Module2Condition]],
        help="1=MANUAL, 2=NoA; 생략하면 터미널에서 선택",
    )
    args = parser.parse_args(argv)
    os.chdir(PROJECT_ROOT)
    try:
        settings = load_pilot_settings(args.config)
        participant_id = select_participant(args.participant_id)
        session_type = select_session_type(args.session_type)
        settings["m1"] = select_m1_assignment(args.m1)
        settings["m2"] = select_m2_assignment(args.m2)
        run_id = reserve_pilot_run(Path("data/p8_operator"), participant_id, session_type)
        register_supervised_run(Path("data/p8_operator") / run_id, participant_id)
    except KeyboardInterrupt:
        print("설정 입력을 취소했어. 주행은 시작되지 않았어.")
        return 130
    except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
        print(f"파일럿 설정을 확인해 줘: {error}")
        return 2
    print(f"참가자 번호: {participant_id}\n저장 유형: {session_type}\nRun ID: {run_id}", flush=True)
    print(f"결과 ZIP 예정 경로: {(Path('data/p8_operator/_exports') / (run_id + '.zip')).resolve()}", flush=True)
    print(
        f"훈련 600초 → M1 600초 ({settings['m1']}) → 휴식 → "
        f"M2 780초 ({settings['m2']}) → 결과 ZIP 저장",
        flush=True,
    )
    print(
        "M1 SuRT 기록은 관리자가 선택한 배정입니다. "
        "별도 SuRT 앱의 실제 수행·반응 기록은 앱에서 따로 저장해 주세요.",
        flush=True,
    )
    print(
        "훈련 조작 안내: NoA가 켜져 있을 때 깜빡이를 켜면 해당 방향의 인접 차로로 이동합니다.\n"
        "여러 차로를 이동할 때는 차선 변경 완료 후 깜빡이를 다시 켜 주세요.\n"
        "출구 안내에 따라 우측 깜빡이로 가장 오른쪽 진출 차로에 도달하면 NoA가 진출을 이어갑니다.",
        flush=True,
    )
    operator_args = build_operator_args(settings, participant_id=participant_id, run_id=run_id)
    operator_args += ["--session-type", session_type]
    if not args.no_gaze:
        operator_args += ["--gaze-config", str(args.gaze_config)]
    return operator_cli.main(operator_args)


if __name__ == "__main__":
    raise SystemExit(main())
