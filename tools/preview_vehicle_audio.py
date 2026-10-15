"""Offline synthetic trajectory -> WAV. Never opens an audio device or CARLA.

Run from repository root with python -m tools.preview_vehicle_audio --output-dir ...
"""

from __future__ import annotations

import argparse
import json
import time
import wave
from array import array
from dataclasses import asdict
from pathlib import Path

from src.scenario.vehicle_audio_assets import (
    GRAIN_SECONDS,
    HOP_SECONDS,
    procedural_bank,
    voice_mix,
)
from src.scenario.vehicle_audio_model import EngineSoundModel, VehicleAudioState


def trajectory(now: float) -> VehicleAudioState:
    if now < 3:
        speed, throttle, brake = 0.0, 0.0, 0.0
    elif now < 13:
        speed, throttle, brake = (now - 3) * 6, 0.75, 0.0
    elif now < 18:
        speed, throttle, brake = 60.0, 0.18, 0.0
    elif now < 26:
        speed, throttle, brake = 60 + (now - 18) * 5, 0.85, 0.0
    elif now < 36:
        speed, throttle, brake = 100 - (now - 26) * 10, 0.0, 0.5
    else:
        speed, throttle, brake = 0.0, 0.0, 1.0
    gear = min(6, 1 + int(speed / 22))
    return VehicleAudioState(
        speed,
        throttle,
        brake,
        gear,
        False,
        now,
        "synthetic-hero",
        "offline-preview",
        "NOA_ACTIVE" if 15 <= now < 29 else "MANUAL",
    )


def render_preview(output_dir: Path, sample_rate: int = 22050) -> dict:
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    bank = procedural_bank(sample_rate, 1)
    prepared = time.perf_counter()
    clips = tuple(array("h", pcm) for pcm in bank.pcm)
    duration = 39.0
    output = array("f", [0.0]) * round((duration + GRAIN_SECONDS) * sample_rate)
    model = EngineSoundModel()
    trace = []
    next_grain = 0.0
    timings = []
    for frame in range(round(duration * 60)):
        now = frame / 60
        current = trajectory(now)
        tick = time.perf_counter()
        parameters = model.update(current, now)
        mix = voice_mix(parameters, now)
        timings.append(time.perf_counter() - tick)
        if now + 1e-9 < next_grain:
            continue
        next_grain += HOP_SECONDS
        offset = round(now * sample_rate)
        for asset, gain in mix:
            if gain <= 0.0001:
                continue
            for i, value in enumerate(clips[asset]):
                output[offset + i] += gain * value
        trace.append(
            {"time": now, "state": asdict(current), "sound": asdict(parameters)}
        )
    peak = max(abs(v) for v in output)
    clipped_samples = sum(abs(v) > 32767 for v in output)
    pcm = array("h", (round(max(-32767, min(32767, v))) for v in output))
    with (
        (output_dir / "synthetic_drive.wav").open("xb") as file,
        wave.open(file, "wb") as wav,
    ):
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    report = {
        "source": "DETERMINISTIC_SYNTHETIC_TRAJECTORY_NOT_CARLA_RECORDING",
        "asset": bank.asset_id,
        "license": "Original project procedural synthesis; no third-party recordings",
        "sample_rate": sample_rate,
        "duration_seconds": len(output) / sample_rate,
        "peak_pcm": peak,
        "clipped_samples": clipped_samples,
        "bank_preparation_seconds": prepared - started,
        "model_and_mix_mean_ms": sum(timings) / len(timings) * 1000,
        "model_and_mix_max_ms": max(timings) * 1000,
        "model_updates": len(timings),
        "render_wall_seconds": time.perf_counter() - started,
        "playback_backend_or_live_fps_measured": False,
        "intervals": {
            "idle": [0, 3],
            "0_to_60": [3, 13],
            "cruise60": [13, 18],
            "60_to_100": [18, 26],
            "100_to_0": [26, 36],
            "idle_end": [36, 39],
            "noa": [15, 29],
        },
        "trace": trace,
    }
    with (output_dir / "preview_manifest.json").open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, allow_nan=False, indent=2)
    return {key: value for key, value in report.items() if key != "trace"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="new preview directory"
    )
    args = parser.parse_args()
    print(json.dumps(render_preview(args.output_dir), indent=2))


if __name__ == "__main__":
    main()
