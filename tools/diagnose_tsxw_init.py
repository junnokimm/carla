"""User-started, one-stage-per-process Pygame initialization diagnosis.

No application FFB commands. SDL initialization/open may still reset/acquire
the device internally. Never run this against hardware in automated tests.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

STAGES = {
    "A": "import pygame only; no initialization",
    "B": "display.init + minimal window; window event pump only",
    "C": "joystick.init only; no explicit device open or event pump",
    "D": "pygame.init only; no explicit device open or event pump",
    "E": "display.init (no window) + joystick.init + Joystick(index); no event pump or axis/button reads",
    "F": "same prerequisites as E + event pump + axis/button/hat reads",
}
WARNING = (
    "C/D/E/F: Joystick initialization/open can change device ownership and existing "
    "force state. SDL may internally send RESET/autocenter commands even though "
    "this tool creates no effects and calls no rumble/haptic/autocenter API. "
    "E/F open exactly the selected index once (default 0); name validation happens "
    "AFTER open. Do not run alongside P8, another input test, or an experiment. "
    "Observe the wheel yourself; this tool cannot measure force or prove OS "
    "exclusive ownership has been released. No automatic retries/reconnection."
)


def error_payload(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


class Recorder:
    def __init__(self, stream: TextIO, stage: str) -> None:
        self.stream = stream
        self.stage = stage

    def __call__(self, event: str, **values: Any) -> None:
        row = {
            "schema_version": 1,
            "stage": self.stage,
            "pid": os.getpid(),
            "host_monotonic_ns": time.monotonic_ns(),
            "host_utc_ns": time.time_ns(),
            "event": event,
            **values,
        }
        line = json.dumps(row, ensure_ascii=True, allow_nan=False)
        self.stream.write(line + "\n")
        self.stream.flush()
        print(line, flush=True)


def state(pygame: Any, device: Any = None) -> dict[str, Any]:
    return {
        "pygame_initialized": pygame.get_init(),
        "display_initialized": pygame.display.get_init(),
        "joystick_initialized": pygame.joystick.get_init(),
        "device_initialized": None if device is None else device.get_init(),
    }


def run_stage(
    stage: str,
    seconds: float,
    index: int,
    emit: Callable[..., None],
    *,
    loader: Callable[[], Any] = lambda: importlib.import_module("pygame"),
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run only after explicit user confirmation; loader is replaced in tests."""
    pygame = device = None
    failed = False
    cleanup_errors: list[dict[str, str]] = []
    step = "import_pygame"

    def call(name: str, action: Callable[[], Any]) -> Any:
        nonlocal step
        step = name
        emit("call_started", operation=name, state=state(pygame, device))
        result = action()
        emit("call_completed", operation=name, state=state(pygame, device))
        return result

    try:
        if stage not in STAGES or not math.isfinite(seconds) or not 0 < seconds <= 120:
            raise ValueError("stage A-F and observation duration in (0, 120] required")
        if index < 0:
            raise ValueError("device index must be nonnegative")
        emit("call_started", operation=step)
        pygame = loader()
        emit(
            "import_completed",
            pygame_version=pygame.version.ver,
            sdl_version=pygame.get_sdl_version(),
            pygame_path=pygame.__file__,
            state=state(pygame),
        )
        if any(state(pygame).values()):
            raise RuntimeError(
                "fresh process required; an SDL subsystem is already initialized"
            )
        if stage in ("B", "E", "F"):
            call("display.init", pygame.display.init)
        if stage == "B":
            call("display.set_mode", lambda: pygame.display.set_mode((480, 160)))
            pygame.display.set_caption("TS-XW diagnostic B - close to end")
            pygame.display.flip()
        if stage in ("C", "E", "F"):
            call("joystick.init", pygame.joystick.init)
        if stage == "D":
            initialized, failures = call("pygame.init", pygame.init)
            emit(
                "pygame_init_result",
                initialized_modules=initialized,
                failed_modules=failures,
            )
            if failures:
                raise RuntimeError(f"pygame.init reported {failures} failed modules")
        if stage in ("E", "F"):
            step = "Joystick"
            emit("call_started", operation=step, state=state(pygame))
            device = pygame.joystick.Joystick(index)
            emit("call_completed", operation=step, state=state(pygame, device))
            step = "device_identity"
            name = device.get_name()
            instance_id = device.get_instance_id()
            emit(
                "device_opened",
                index=index,
                name=name,
                instance_id=instance_id,
                state=state(pygame, device),
            )
            if name != "TS-XW Racer":
                raise RuntimeError(
                    f"opened device is not TS-XW Racer: {name!r}; stopping"
                )
        emit("observation_started", seconds=seconds, force_measurement="NOT_MEASURED")
        deadline = clock() + seconds
        samples = 0
        last_sample = None
        if stage == "F":
            step = "input_shape"
            shape = (
                device.get_numaxes(),
                device.get_numbuttons(),
                device.get_numhats(),
            )
            emit("input_shape", axes=shape[0], buttons=shape[1], hats=shape[2])
            if shape != (4, 13, 1):
                raise RuntimeError(f"unexpected TS-XW input shape: {shape}")
        while clock() < deadline:
            if stage == "B":
                step = "window_event_pump"
                if any(event.type == pygame.QUIT for event in pygame.event.get()):
                    emit("user_window_close")
                    break
            elif stage == "F":
                step = "input_poll"
                events = pygame.event.get()  # Pumps SDL input; E deliberately does not.
                if any(
                    event.type == pygame.JOYDEVICEREMOVED
                    and event.dict.get("instance_id") == instance_id
                    for event in events
                ):
                    raise RuntimeError("selected TS-XW removed")
                if not device.get_init() or device.get_instance_id() != instance_id:
                    raise RuntimeError("selected TS-XW closed or instance changed")
                axes = [device.get_axis(i) for i in range(shape[0])]
                if not all(math.isfinite(value) for value in axes):
                    raise RuntimeError("nonfinite axis sample")
                last_sample = {
                    "axes": axes,
                    "buttons": [device.get_button(i) for i in range(shape[1])],
                    "hats": [device.get_hat(i) for i in range(shape[2])],
                }
                samples += 1
            sleep(min(0.05, max(0.0, deadline - clock())))
        emit("observation_completed", poll_count=samples, last_input=last_sample)
    except BaseException as error:  # noqa: BLE001 -- record all stage failures, then release resources
        failed = True
        emit("stage_failed", operation=step, error=error_payload(error))
    finally:
        # Cleanup is attempted even if a preceding cleanup step fails. No retry.
        actions = []
        if device is not None:
            actions.append(("device.quit", device.quit))
        if pygame is not None:
            actions.extend(
                (
                    ("joystick.quit", pygame.joystick.quit),
                    ("pygame.quit", pygame.quit),
                )
            )
        for name, action in actions:
            try:
                action()
            except BaseException as error:  # noqa: BLE001 -- try remaining cleanup even after interruption
                cleanup_errors.append({"operation": name, **error_payload(error)})
        final_state = None
        if pygame is not None:
            try:
                final_state = state(pygame, device)
                if any(final_state.values()):
                    cleanup_errors.append(
                        {
                            "operation": "state_check",
                            "message": "SDL/device still initialized",
                        }
                    )
            except BaseException as error:  # noqa: BLE001 -- failed verification must not imply release
                cleanup_errors.append(
                    {"operation": "state_check", **error_payload(error)}
                )
        emit(
            "cleanup_completed",
            success=not cleanup_errors,
            attempted=[name for name, _ in actions],
            errors=cleanup_errors,
            state=final_state,
            os_ownership_released="NOT_VERIFIED",
        )
    code = 1 if failed or cleanup_errors else 0
    emit("stage_finished", exit_code=code, force_measurement="NOT_MEASURED")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new JSONL file; existing files are never overwritten",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 120 or args.index < 0:
        parser.error("seconds must be in (0, 120]; index must be nonnegative")
    print(f"Stage {args.stage}: {STAGES[args.stage]}\n{WARNING}", flush=True)
    print(
        "주의: 초기화/Open만으로도 기존 힘·장치 점유 상태가 바뀔 수 있습니다. "
        "장치 움직임에 대비하고, 힘의 유지·복원 여부는 직접 관찰하십시오.",
        flush=True,
    )
    try:
        with args.output.open("x", encoding="utf-8") as stream:
            emit = Recorder(stream, args.stage)
            emit(
                "selected",
                description=STAGES[args.stage],
                seconds=args.seconds,
                index=args.index,
            )
            try:
                answer = input(
                    f"Type START {args.stage} to run this stage, anything else cancels: "
                )
            except (EOFError, KeyboardInterrupt) as error:
                emit("cancelled_before_import", error=error_payload(error))
                return 2
            if answer.strip() != f"START {args.stage}":
                emit("cancelled_before_import")
                return 2
            emit("user_confirmed")
            code = run_stage(args.stage, args.seconds, args.index, emit)
            # Observation is requested only AFTER releasing the device/subsystems.
            try:
                observation = input(
                    "During observation: force maintained/lost/changed/unknown? "
                    "After cleanup: restored/not restored/unknown? Optional notes: "
                )
                emit(
                    "user_observation",
                    source="USER_REPORT_NOT_SENSOR",
                    text=observation,
                )
            except (EOFError, KeyboardInterrupt):
                emit("user_observation_missing")
            emit("process_exit_requested", exit_code=code)
            print(
                "After this process exits, verify force restoration yourself. OS ownership was not measured."
            )
            return code
    except (EOFError, KeyboardInterrupt):
        return 2
    except OSError as error:
        print(f"Diagnostic log I/O failure: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
