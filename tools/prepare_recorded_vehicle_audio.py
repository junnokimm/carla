"""Offline preparation of the approved CC0 previews; never run by the pilot.

Usage: python -B -m tools.prepare_recorded_vehicle_audio
Only a dummy audio device is opened. Originals and provenance are retained.
"""

import hashlib
import json
import math
import os
import shutil
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/audio_audition/20261021-honda-crv"
DESTINATION = ROOT / "assets/audio/honda_crv"
RATE = 44100
CHANNELS = 2
ASSET_ID = "honda-crv-recorded-preview-v1"
# Leave encoder padding out; the loaded acceleration portion excludes initial idle.
SECTIONS = (("idle", 2.0, 14.5), ("acceleration", 4.0, 10.5), ("cruise", 2.0, 14.5))


def prepare_loop(pcm, start, end):
    values = array("h", pcm)[
        round(start * RATE) * CHANNELS : round(end * RATE) * CHANNELS
    ]
    fade_frames = RATE // 2
    fade = fade_frames * CHANNELS
    # Join the tail to the head with a raised-cosine overlap, then wrap at a
    # continuous sample boundary. No synthetic tones or additional noise.
    result = array("h", values[fade:-fade])
    for index in range(fade_frames):
        weight = (1 - math.cos(math.pi * index / (fade_frames - 1))) / 2
        for channel in range(CHANNELS):
            offset = index * CHANNELS + channel
            result.append(
                round(values[-fade + offset] * (1 - weight) + values[offset] * weight)
            )
    rms = math.sqrt(sum(v * v for v in result) / len(result))
    gain = min(32768 * 10 ** (-25 / 20) / rms, 12000 / max(abs(v) for v in result))
    result = array("h", (round(v * gain) for v in result))
    return result.tobytes(), gain


def main():
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    import pygame

    DESTINATION.mkdir(parents=True, exist_ok=True)
    originals = DESTINATION / "originals"
    originals.mkdir(exist_ok=True)
    sources = json.loads((SOURCE / "sources.json").read_text(encoding="utf-8"))
    manifest = {
        "asset_id": ASSET_ID,
        "license": "CC0 1.0",
        "author": "AndrewAlexander",
        "vehicle": "Honda CR-V",
        "input_type": "PUBLIC_HQ_MP3_PREVIEWS",
        "processing": "Trim; 0.5 s raised-cosine loop seam; RMS -25 dBFS with peak <= 12000; PCM16 WAV. No synthesis or pitch shift.",
        "rate": RATE,
        "channels": CHANNELS,
        "chunk_seconds": 1.0,
        "layers": [],
    }
    pygame.mixer.init(frequency=RATE, size=-16, channels=CHANNELS)
    try:
        for (name, start, end), source in zip(SECTIONS, sources["files"], strict=True):
            path = SOURCE / source["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
            shutil.copyfile(path, originals / path.name)
            pcm, gain = prepare_loop(
                pygame.mixer.Sound(file=str(path)).get_raw(), start, end
            )
            output = DESTINATION / f"{name}.wav"
            with wave.open(str(output), "wb") as writer:
                writer.setnchannels(CHANNELS)
                writer.setsampwidth(2)
                writer.setframerate(RATE)
                writer.writeframes(pcm)
            manifest["layers"].append(
                {
                    "name": name,
                    "file": output.name,
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "duration_s": len(pcm) / (RATE * CHANNELS * 2),
                    "trim_start_s": start,
                    "trim_end_s": end,
                    "normalization_gain": gain,
                    "source": source,
                }
            )
    finally:
        pygame.mixer.quit()
    (DESTINATION / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
