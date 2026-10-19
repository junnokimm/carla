"""Standalone 65-inch monitor AOI and physical-tablet tracking recheck.

Only the temporary preview vehicle is created/frozen/destroyed. Existing pilot,
vehicle input, AOI classifier and Tobii capture implementations are reused unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
TABLET_TARGETS = (
    ("forward_before", "주행 모니터 중앙"),
    ("tablet_center", "실제 태블릿 중앙"),
    ("tablet_top_left", "실제 태블릿 화면 왼쪽 위 모서리"),
    ("tablet_top_right", "실제 태블릿 화면 오른쪽 위 모서리"),
    ("tablet_bottom_left", "실제 태블릿 화면 왼쪽 아래 모서리"),
    ("tablet_bottom_right", "실제 태블릿 화면 오른쪽 아래 모서리"),
    ("forward_after", "다시 주행 모니터 중앙"),
)


class UserStopped(RuntimeError):
    pass


def save_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def prompt(message: str) -> str:
    answer = input(message).strip()
    if answer.lower() == "q":
        raise UserStopped("USER_STOPPED")
    return answer


def confirm(message: str) -> None:
    if prompt(message + " [y/N, q: 종료]: ").lower() != "y":
        raise UserStopped("SETUP_NOT_CONFIRMED")


def child(command: list[str], log: Path, timeout: int) -> int:
    print(f"실행 중 · 기록: {log}", flush=True)
    try:
        with log.open("x", encoding="utf-8") as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
    finally:
        if log.exists():
            print(log.read_text(encoding="utf-8", errors="replace").strip(), flush=True)
    return result.returncode


def display_snapshot(python: Path, serial: str | None, directory: Path) -> dict:
    command = [str(python), "-B", "-m", "tools.tobii.display_area_smoke"]
    if serial:
        command += ["--serial", serial]
    log = directory / "display_area_console.txt"
    if child(command, log, 15):
        raise RuntimeError("Tobii 표시 영역을 읽지 못했습니다. display_area_console.txt를 확인하세요.")
    report = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    if report.get("status") != "DISPLAY_AREA_READ":
        raise RuntimeError("DISPLAY_AREA_READ_FAILED")
    save_json(directory / "display_area.json", report)
    width, height = float(report["width_mm"]), float(report["height_mm"])
    print(f"저장된 표시 영역: {width:.1f} × {height:.1f} mm")
    # A setup typo guard only, not a calibration or physical-pose accuracy criterion.
    if not (1367 <= width <= 1511 and 769 <= height <= 849):
        raise RuntimeError("65인치 16:9 화면 크기와 설정이 다릅니다. ETM Advanced setup을 확인하세요.")
    return report


def setup_confirmation(directory: Path) -> None:
    print("\n실험은 종료하고 실제 주행 좌석·태블릿 위치를 유지하세요. 언제든 q로 종료할 수 있습니다.")
    distance = float(prompt("현재 눈부터 Nano 앞면까지 실측 거리(cm): "))
    if not math.isfinite(distance) or not 45 <= distance <= 85:
        raise RuntimeError("먼저 눈–Nano 거리를 45~85cm 범위로 조정하고 ETM 설정·보정을 확인하세요.")
    confirm("새 표시 영역 설정으로 현재 피험자의 Calibration까지 완료했나요?")
    save_json(directory / "setup.json", {
        "eye_to_tracker_cm_user_reported": distance,
        "participant_calibration_user_confirmed": True,
        "screen_diagonal_in_user_reported": 65, "tablet": "SEPARATE_PHYSICAL_DEVICE",
        "geometry_or_accuracy_independently_verified": False,
    })


def spawn_preview(world):
    carla_map = world.get_map()
    if "Town04" not in carla_map.name:
        raise RuntimeError("Unreal에서 Town04를 열고 Play를 누르세요.")
    if world.get_settings().synchronous_mode:
        raise RuntimeError("동기 모드 실험이 실행 중입니다. 실험 종료 후 비동기 월드에서 재시도하세요.")
    points = carla_map.get_spawn_points()
    if not points:
        raise RuntimeError("Town04 spawn point가 없습니다.")
    preferred = 344 if len(points) > 344 else 0
    origin = points[preferred].location
    nearby = sorted((i for i in range(len(points)) if i != preferred),
                    key=lambda i: points[i].location.distance(origin))
    blueprint = world.get_blueprint_library().find("vehicle.mercedes.coupe_2020")
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", "aoi_validation_temp")
    for index in [preferred] + nearby[:29]:
        actor = world.try_spawn_actor(blueprint, points[index])
        if actor is not None:
            print(f"검증 전용 정차 차량 생성: actor={actor.id}, spawn={index}")
            return actor
    raise RuntimeError("주변 30개 지점에 빈 공간이 없습니다. 기존 차량은 삭제하지 않았습니다.")


def monitor_checks(directory: Path, tobii_python: Path, serial: str, repeats: int,
                   report: dict) -> None:
    import carla

    confirm("run_pilot은 종료했고 Unreal Town04에서 Play 중인가요?")
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(10.0)
    actor = spawn_preview(client.get_world())
    try:
        actor.set_simulate_physics(False)
        for repeat in range(1, repeats + 1):
            name = f"monitor_{repeat:02d}"
            print(f"\n[전방·미러 {repeat}/{repeats}] 주행 모니터 전체화면에서 C로 확인합니다.")
            print("전방 → 좌측 미러 → 우측 미러 → 눈 감기. 각 대상을 주시한 뒤 진행자가 Space.")
            print("각 2초 측정입니다. 다른 화면이면 C 전에 F11 → 창 이동 → F11. ESC: 중단.")
            prompt("준비되면 Enter (q: 전체 검증 종료): ")
            code = child([
                sys.executable, "-B", "-m", "tools.tobii.aoi_validation",
                "--tobii-python", str(tobii_python), "--tobii-serial", serial,
                "--actor-id", str(actor.id), "--aoi", str(ROOT / "config/p6_aoi_rear_disabled_candidate_v1.json"),
                "--max-seconds", "180", "--output-root", str(directory / "aoi"), "--run-id", name,
            ], directory / f"{name}_console.txt", 225)
            report["monitor_runs"].append({"name": name, "exit_code": code})
            if code:
                raise RuntimeError(f"{name} 검증이 완료되지 않았습니다. 원본과 콘솔 기록을 보존했습니다.")
    finally:
        # Never enumerate/delete any pre-existing vehicle, or change world/TM settings.
        if actor.is_alive and not actor.destroy():
            raise RuntimeError(f"검증 차량 정리 실패: actor={actor.id}")


def tablet_profile_snapshot(directory: Path) -> dict:
    """Keep the pre-capture mapping for later independent offline comparison."""
    config_path = ROOT / "config/p8_gaze.json"
    if not config_path.exists():
        return {"status": "NOT_CONFIGURED"}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not config.get("tablet_profile"):
            return {"status": "NOT_CONFIGURED"}
        source = config_path.parent / config["tablet_profile"]
        raw = source.read_bytes()
        profile = json.loads(raw.decode("utf-8-sig"))
        destination = directory / "tablet_profile_snapshot.json"
        with destination.open("xb") as stream:
            stream.write(raw)
        return {"status": "SNAPSHOT_ONLY_NOT_VALIDATED", "source": str(source.resolve()),
                "file": destination.name, "sha256": hashlib.sha256(raw).hexdigest(),
                "profile_id": profile.get("profile_id")}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        # Raw collection remains useful even if the optional profile is unavailable.
        print(f"태블릿 매핑 사본 저장 실패 · 원본 시선 수집은 계속합니다: {error}")
        return {"status": "UNAVAILABLE", "error": str(error)}


def tablet_checks(directory: Path, tobii_python: Path, serial: str, report: dict,
                  *, seconds: int = 20) -> None:
    destination = directory / "tablet"
    destination.mkdir()
    print(f"\n[태블릿 추적 확인] 각 위치 {seconds}초. 평소 자세로 바라보고 진행자가 Enter를 누르세요.")
    print("화면에 태블릿 영상이 생기지는 않습니다. 실제 태블릿을 봅니다.")
    print("이 단계는 원본 시선을 수집합니다. 영역 분류 정확도는 수집 후 고정된 매핑과 비교합니다.")
    for target, label in TABLET_TARGETS:
        prompt(f"{label}을 바라본 상태에서 Enter (q: 종료): ")
        code = child([
            str(tobii_python), "-B", "-m", "tools.tobii.gaze_smoke", "--serial", serial,
            "--seconds", str(seconds), "--output", str(destination / f"{target}.jsonl"),
        ], directory / f"{target}_console.txt", seconds + 20)
        report["tablet_runs"].append({"target": target, "exit_code": code, "requested_seconds": seconds})
        if code:
            raise RuntimeError(f"{target} 시선 수집 실패. 파일을 보존했습니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="Omit when exactly one Nano is connected")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--tablet-only", action="store_true", help="Repeat physical-tablet capture without CARLA")
    parser.add_argument("--tablet-seconds", type=int, choices=range(10, 21), default=20,
                        help="Capture duration per tablet/front target (default: 20)")
    args = parser.parse_args(argv)
    tobii_python = ROOT / ".venv-tobii/Scripts/python.exe"
    directory = ROOT / "data/gaze_recheck" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                              + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    print(f"이번 검증 기록: {directory}", flush=True)
    report = {"status": "STARTED", "requested_monitor_repeats": 0 if args.tablet_only else args.repeats,
              "monitor_runs": [], "tablet_runs": [], "errors": [], "aoi_validated": False,
              "tablet_aoi_implemented": False, "scope": "DEVELOPMENT_RECHECK",
              "tablet_seconds_per_target": args.tablet_seconds}
    code = 2
    try:
        report["tablet_profile_snapshot"] = tablet_profile_snapshot(directory)
        geometry = display_snapshot(tobii_python, args.serial, directory)
        setup_confirmation(directory)
        serial = geometry["serial_number"]
        if not args.tablet_only:
            try:
                monitor_checks(directory, tobii_python, serial, args.repeats, report)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                if isinstance(error, UserStopped):
                    raise
                report["errors"].append(str(error))
                print(f"전방·미러 단계 미완료: {error}")
                confirm("태블릿 추적 확인만 계속할까요?")
        tablet_checks(directory, tobii_python, serial, report, seconds=args.tablet_seconds)
        report["status"] = "CAPTURED_NOT_VALIDATED" if not report["errors"] else "PARTIAL"
        code = 0 if not report["errors"] else 2
    except (KeyboardInterrupt, EOFError, UserStopped):
        report["status"] = "USER_STOPPED"
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        report["status"] = "FAILED"
        report["errors"].append(str(error))
        print(f"검증 중단: {error}")
    finally:
        save_json(directory / "summary.json", report)
        print(f"\n저장 위치: {directory}\n상태: {report['status']}")
        print("수집 완료는 영역 정확도 합격을 뜻하지 않습니다. 이 폴더를 기준으로 결과를 분석합니다.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
