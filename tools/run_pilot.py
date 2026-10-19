"""Run the existing P8 operator with the recorded local pilot settings."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from src.experiment.context import Module1Condition, Module2Condition
from src.p8 import operator_cli
from src.p8.crash_recovery import register_supervised_run
from src.scenario.research_exit_config import ExitEligibilityWindow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_CONFIG = Path("config/p8_pilot.json")
M2_CHOICES = {"1": "MANUAL", "2": "NOA_L2"}


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
        "5",
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
    parser.add_argument("--participant-id")
    parser.add_argument("--m1", choices=[item.value for item in Module1Condition])
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
        settings["m2"] = select_m2_assignment(args.m2)
    except KeyboardInterrupt:
        print("설정 입력을 취소했어. 주행은 시작되지 않았어.")
        return 130
    except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
        print(f"파일럿 설정을 확인해 줘: {error}")
        return 2
    if args.m1 is not None:
        settings["m1"] = args.m1
    tag = uuid4().hex
    participant_id = args.participant_id or f"PILOT-{tag}"
    run_id = f"P8-PILOT-{tag}"
    register_supervised_run(Path("data/p8_operator") / run_id, participant_id)
    print(f"파일럿 ID: {participant_id}\nRun ID: {run_id}", flush=True)
    print(
        f"훈련 600초 → M1 600초 ({settings['m1']}) → 휴식 → "
        f"M2 780초 ({settings['m2']}) → 결과 ZIP 저장",
        flush=True,
    )
    operator_args = build_operator_args(settings, participant_id=participant_id, run_id=run_id)
    if not args.no_gaze:
        operator_args += ["--gaze-config", str(args.gaze_config)]
    return operator_cli.main(operator_args)


if __name__ == "__main__":
    raise SystemExit(main())
