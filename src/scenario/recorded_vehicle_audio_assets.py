"""Load and validate the approved WAV loops before the driving loop starts."""

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path

ASSET_ID = "honda-crv-recorded-preview-v1"
ASSET_DIRECTORY = Path(__file__).resolve().parents[2] / "assets/audio/honda_crv"
LAYERS = ("idle", "acceleration", "cruise")
CHUNK_SECONDS = 1.0


@dataclass(frozen=True)
class RecordedAudioBank:
    layers: tuple[tuple[bytes, ...], ...]
    manifest_sha256: str


def recorded_bank(mixer, config, directory=ASSET_DIRECTORY):
    rate, size, channels = config
    if size != -16 or not 8000 <= rate <= 96000 or channels not in (1, 2):
        raise ValueError("recorded audio requires signed 16-bit mono/stereo PCM")
    data = (directory / "manifest.json").read_bytes()
    manifest = json.loads(data)
    if (
        manifest["asset_id"] != ASSET_ID
        or tuple(layer["name"] for layer in manifest["layers"]) != LAYERS
    ):
        raise ValueError("unexpected recorded vehicle audio manifest")
    layers = []
    chunk_bytes = round(rate * CHUNK_SECONDS) * channels * 2
    for name, metadata in zip(LAYERS, manifest["layers"], strict=True):
        path = directory / f"{name}.wav"
        if path.stat().st_size > 12_000_000:
            raise ValueError("recorded audio asset exceeds preload limit")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise ValueError(f"recorded audio checksum mismatch: {name}")
        with wave.open(str(path), "rb") as reader:
            if (
                reader.getsampwidth() != 2
                or reader.getnchannels() != 2
                or reader.getframerate() != 44100
            ):
                raise ValueError("unexpected source PCM format")
        # SDL converts once, before driving, to the existing mixer format.
        pcm = mixer.Sound(file=str(path)).get_raw()
        if len(pcm) % chunk_bytes or not 2 <= len(pcm) // chunk_bytes <= 30:
            raise ValueError("recorded audio loop does not have whole finite chunks")
        layers.append(
            tuple(pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes))
        )
    return RecordedAudioBank(tuple(layers), hashlib.sha256(data).hexdigest())
