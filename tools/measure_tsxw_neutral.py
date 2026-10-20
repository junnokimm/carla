"""User-operated neutral measurement; no CARLA connection or calibration writes."""

from __future__ import annotations

import csv
import json
import statistics
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROUNDS = (
    ("level", "Hold the wheel physically level."),
    ("from_left", "Turn slightly LEFT, then return to level."),
    ("from_right", "Turn slightly RIGHT, then return to level."),
)
FIELDS = (
    "round", "elapsed_s", "host_perf_counter_ns", "raw_steering",
    "manual_steering", "noa_steering_engaged",
)


def summarize(rows: list[dict]) -> list[dict]:
    results = []
    for name, _ in ROUNDS:
        samples = [r for r in rows if r["round"] == name]
        if not samples:
            continue
        raw = [r["raw_steering"] for r in samples]
        results.append({
            "round": name,
            "samples": len(raw),
            "sample_span_s": samples[-1]["elapsed_s"] - samples[0]["elapsed_s"],
            "raw_min": min(raw),
            "raw_max": max(raw),
            "raw_mean": statistics.mean(raw),
            "raw_median": statistics.median(raw),
            "raw_std": statistics.pstdev(raw),
            "manual_nonzero_samples": sum(r["manual_steering"] != 0 for r in samples),
        })
    return results


def main() -> int:
    # Same pre-import SDL policy and input adapter as run_pilot.cmd.
    from src.p8.tsxw_sdl import preserve_existing_resistance

    policy = preserve_existing_resistance()
    print(json.dumps(policy), flush=True)
    if policy["status"] != "TSXW_RESISTANCE_PRESERVE_ENABLED":
        print("Measurement cancelled: the experiment's SDL policy is unavailable.")
        return 2

    import pygame

    from src.p8.tsxw_input import TsxwInput
    from src.p8.tsxw_policy import PILOT_MANUAL_STEERING_CENTER_AXIS, TsxwConfig

    config = TsxwConfig(
        index=0, steering_deadzone=0.02, manual_steering_deadzone=0.005,
        manual_steering_center_axis=PILOT_MANUAL_STEERING_CENTER_AXIS,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = (
        Path(__file__).resolve().parents[1]
        / "data" / "steering_neutral" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    print(f"OUTPUT: {directory}", flush=True)
    rows: list[dict] = []
    device_events: list[dict] = []
    completed_rounds: list[str] = []
    adapter = None
    error = None
    cleanup_errors = []
    status = "INCOMPLETE"
    try:
        pygame.display.init()
        pygame.font.init()
        screen = pygame.display.set_mode((840, 300))
        pygame.display.set_caption("TS-XW neutral measurement - SPACE / ESC")
        font = pygame.font.Font(None, 30)
        clock = pygame.time.Clock()
        adapter = TsxwInput.open(
            config,
            event_sink=lambda event: device_events.append({
                "type": event.event_type,
                "payload": json.loads(event.payload_json),
            }),
        )
        with (directory / "samples.csv").open(
            "x", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for number, (name, instruction) in enumerate(ROUNDS, 1):
                started = None
                while True:
                    events = pygame.event.get()
                    if any(
                        event.type == pygame.QUIT
                        or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
                        for event in events
                    ):
                        raise KeyboardInterrupt
                    focused = bool(pygame.key.get_focused())
                    if started is not None and not focused:
                        raise RuntimeError("WINDOW_FOCUS_LOST: keep the measurement window active and retry")
                    sample = adapter.poll(events)
                    now = time.perf_counter()
                    if started is None and focused and any(
                        event.type == pygame.KEYDOWN
                        and event.key == pygame.K_SPACE
                        and not event.dict.get("repeat", 0)
                        for event in events
                    ):
                        started = now
                    elapsed = None if started is None else now - started
                    if elapsed is not None and elapsed >= 8.0:
                        stream.flush()
                        completed_rounds.append(name)
                        break
                    if elapsed is not None and elapsed >= 3.0:
                        row = {
                            "round": name,
                            "elapsed_s": elapsed - 3.0,
                            "host_perf_counter_ns": time.perf_counter_ns(),
                            "raw_steering": sample.raw_steering,
                            "manual_steering": sample.steering,
                            "noa_steering_engaged": sample.steering_engaged,
                        }
                        rows.append(row)
                        writer.writerow(row)
                        phase = f"RECORDING: {8.0 - elapsed:.1f}s left - keep level"
                    elif elapsed is not None:
                        phase = f"GET READY: {3.0 - elapsed:.1f}s - keep level"
                    else:
                        phase = "Click this window, then SPACE when level."
                    screen.fill((10, 28, 32))
                    lines = (
                        f"Round {number}/3: {name}",
                        instruction,
                        phase,
                        f"Raw axis: {sample.raw_steering:+.7f}",
                        f"Current manual command: {sample.steering:+.7f}",
                        "Measurement only. ESC cancels. No settings are changed.",
                    )
                    for i, line in enumerate(lines):
                        screen.blit(font.render(line, True, (220, 240, 240)), (20, 22 + 43 * i))
                    pygame.display.flip()
                    clock.tick(60)
        status = "COMPLETE"
    except KeyboardInterrupt:
        status, error = "CANCELLED", "Operator cancelled measurement"
    except Exception as failure:  # noqa: BLE001 -- keep partial diagnostic data
        status, error = "FAILED", f"{type(failure).__name__}: {failure}"
    finally:
        for close in ([adapter.close] if adapter is not None else []) + [pygame.quit]:
            try:
                close()
            except Exception as failure:  # noqa: BLE001 -- attempt remaining cleanup
                cleanup_errors.append(f"{type(failure).__name__}: {failure}")
        report = {
            "status": status,
            "error": error,
            "cleanup_errors": cleanup_errors,
            "calibration_applied": False,
            "center_posture": "USER_INSTRUCTED_NOT_AUTOMATICALLY_VERIFIED",
            "settle_seconds": 3,
            "measurement_seconds_per_round": 5,
            "completed_rounds": completed_rounds,
            "config": asdict(config),
            "sdl_policy": policy,
            "device_events": device_events,
            "rounds": summarize(rows),
        }
        (directory / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        print(json.dumps({
            "status": status, "error": error, "cleanup_errors": cleanup_errors,
            "rounds": report["rounds"], "output": str(directory),
        }, ensure_ascii=False), flush=True)
    return 0 if status == "COMPLETE" and not cleanup_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
