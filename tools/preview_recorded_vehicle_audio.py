"""Render a listenable idle/acceleration/cruise/brake/warning demo without CARLA.

Uses the production mixing model and processed recordings, with a simulated
speed trace. This is not a microphone recording or a physical audibility test.
"""

import argparse
import json
import wave
from array import array
from pathlib import Path

from src.scenario.hazard_warning_audio import DURATION_SECONDS, warning_pcm
from src.scenario.recorded_vehicle_audio import (
    WARNING_DUCK_GAIN,
    WARNING_RELEASE_SECONDS,
)
from src.scenario.recorded_vehicle_audio_assets import ASSET_DIRECTORY, ASSET_ID, LAYERS
from src.scenario.recorded_vehicle_audio_model import RecordedEngineSoundModel
from src.scenario.vehicle_audio_model import VehicleAudioState, clamp


def trace(t):
    if t < 3:
        return 0.0, 0.0, 0.0
    if t < 12:
        return (t - 3) * 10, 0.8, 0.0
    if t < 18:
        return 90.0, 0.25, 0.0
    if t < 24:
        return (24 - t) * 15, 0.0, 0.6
    return 0.0, 0.0, 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/recorded_audio_verification")
    )
    output = parser.parse_args().output_dir
    output.mkdir(parents=True, exist_ok=True)
    layers = []
    for name in LAYERS:
        with wave.open(str(ASSET_DIRECTORY / f"{name}.wav"), "rb") as reader:
            layers.append(array("h", reader.readframes(reader.getnframes())))
    rate, channels, seconds = 44100, 2, 27
    warning = array("h", warning_pcm(rate, channels))
    warning_at = 17.0
    model = RecordedEngineSoundModel()
    samples = array("h")
    schedule = []
    for frame in range(seconds * 60):
        t = frame / 60
        speed, throttle, brake = trace(t)
        state = VehicleAudioState(
            speed, throttle, brake, 3, False, t, "demo", "demo", "MANUAL"
        )
        gains = model.update(state, t).gains
        duck = 1.0
        if t >= warning_at:
            recovery = clamp(
                (t - warning_at - DURATION_SECONDS - 0.25) / WARNING_RELEASE_SECONDS
            )
            duck = WARNING_DUCK_GAIN + (1 - WARNING_DUCK_GAIN) * recovery
        gains = [round(g * duck * 128) / 128 for g in gains]
        if frame % 60 == 0:
            schedule.append({"t": t, "speed_kmh": speed, "gains": gains})
        for index in range(
            frame * rate * channels // 60, (frame + 1) * rate * channels // 60
        ):
            value = sum(
                layer[index % len(layer)] * gain for layer, gain in zip(layers, gains)
            )
            warning_index = index - round(warning_at * rate) * channels
            if 0 <= warning_index < len(warning):
                value += warning[warning_index]
            samples.append(
                round(value)
            )  # Raises rather than silently clipping an offline demo.
    path = output / "recorded_vehicle_demo.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())
    report = {
        "asset": ASSET_ID,
        "duration_s": seconds,
        "warning_at_s": warning_at,
        "peak_pcm16": max(abs(x) for x in samples),
        "schedule": schedule,
        "physical_audibility_verified": False,
        "output": str(path.resolve()),
    }
    (output / "demo.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
