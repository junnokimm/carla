from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from random import Random
from typing import Final

from src.p6.tablet_models import Circle, MediaMetadata, MediaSelection, StimulusConfig

SURT_CIRCLE_COUNT: Final = 7
SURT_STANDARD_DIAMETER_PX: Final = 64
SURT_TARGET_DIAMETER_PX: Final = 72
SURT_CONTRAST: Final = 1.0
_SURT_POSITIONS: Final = (
    (0.18, 0.22),
    (0.50, 0.18),
    (0.82, 0.25),
    (0.28, 0.52),
    (0.70, 0.52),
    (0.20, 0.80),
    (0.76, 0.78),
)


def select_media(filename: str, version: str) -> MediaSelection:
    media_path = Path(filename)
    exists = media_path.is_file()
    digest = None
    if exists:
        hasher = sha256()
        with media_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return MediaSelection(filename, version, exists, digest)


def development_surt_stimulus(rng: Random) -> StimulusConfig:
    target_index = rng.randrange(SURT_CIRCLE_COUNT)
    circles = tuple(
        Circle(
            circle_id=f"circle-{index}",
            x=x,
            y=y,
            diameter_px=(
                SURT_TARGET_DIAMETER_PX
                if index == target_index
                else SURT_STANDARD_DIAMETER_PX
            ),
            target=index == target_index,
        )
        for index, (x, y) in enumerate(_SURT_POSITIONS)
    )
    return StimulusConfig(circles=circles, contrast=SURT_CONTRAST)


def media_metadata_issue(metadata: MediaMetadata) -> str | None:
    if metadata.duration_seconds < 780:
        return "media duration must be at least 780 seconds"
    if not metadata.muted or metadata.loop or metadata.playback_rate != 1.0:
        return "media must be muted, non-looping, at playback rate 1.0"
    return None


def find_circle(stimulus: StimulusConfig | None, circle_id: str) -> Circle | None:
    return next(
        (
            circle
            for circle in (() if stimulus is None else stimulus.circles)
            if circle.circle_id == circle_id
        ),
        None,
    )
