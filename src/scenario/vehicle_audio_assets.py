"""Original procedural PCM; no third-party recordings or vehicle-specific claim.

Finite, windowed grains overlap as a bounded loop. All synthesis is preparation
work, never done in a driving update. Replace this bank provider for licensed PCM.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from functools import lru_cache
from math import cos, exp, pi, sin, tau
from random import Random

from src.scenario.vehicle_audio_model import IDLE_RPM, MAX_RPM, SoundParameters, clamp

GRAIN_SECONDS = 0.64
HOP_SECONDS = 0.16
COHORTS = 6  # four overlaps plus two slots for irregular frame arrivals
VOICES_PER_COHORT = 6  # two RPM anchors x two loads, road, wind
VOICE_COUNT = COHORTS * VOICES_PER_COHORT
RPM_ANCHORS = tuple(IDLE_RPM * (MAX_RPM / IDLE_RPM) ** (i / 18) for i in range(19))
ASSET_ID = "original-procedural-cabin-v1"
PHASE_COUNT = 8
NOISE_INDEX = len(RPM_ANCHORS) * 2 * PHASE_COUNT


@dataclass(frozen=True, slots=True)
class AudioBank:
    sample_rate: int
    channels: int
    pcm: tuple[bytes, ...]  # RPM x light/high x phase; then road, wind. Signed 16-bit.
    asset_id: str = ASSET_ID


def grain_envelope(t: float) -> float:
    # Four regular overlaps sum to 1. Edges approach zero with zero slope.
    return 0.25 * (1.0 - cos(tau * clamp(t / GRAIN_SECONDS)))


@lru_cache(maxsize=1)
def procedural_bank(sample_rate: int, channels: int) -> AudioBank:
    if sample_rate not in (22050, 44100, 48000) or channels not in (1, 2):
        raise ValueError("vehicle audio supports 22050/44100/48000 Hz, mono/stereo")
    count = round(sample_rate * GRAIN_SECONDS)
    envelope = [grain_envelope(i / sample_rate) for i in range(count)]
    clips: list[bytes] = []
    for rpm in RPM_ANCHORS:
        frequency = rpm / 30.0  # generic four-stroke, four-cylinder firing rhythm
        # A band-limited cyclic table avoids repeated trigonometry for phase variants.
        table_size = 4096
        light_table, heavy_table = [], []
        for i in range(table_size):
            phase = tau * i / table_size
            low = high = 0.0
            for harmonic in range(1, 9):
                # Cabin low-pass coloration + different load harmonic falloff.
                cabin = 1.0 / (1.0 + (frequency * harmonic / 1600.0) ** 2)
                wave = sin(harmonic * phase + 0.13 * harmonic) * cabin
                low += wave / harmonic**1.8
                high += wave / harmonic**1.05
            light_table.append(low * 11000)
            heavy_table.append(high * 8500)
        positions = [i * frequency * table_size / sample_rate for i in range(count)]
        for table in (light_table, heavy_table):
            for phase_index in range(PHASE_COUNT):
                samples = array("h")
                shift = phase_index * table_size // PHASE_COUNT
                for i, position in enumerate(positions):
                    base = int(position)
                    fraction = position - base
                    left = (base + shift) % table_size
                    value = (
                        table[left] * (1 - fraction)
                        + table[(left + 1) % table_size] * fraction
                    )
                    samples.extend((round(envelope[i] * value),) * channels)
                clips.append(samples.tobytes())
    random = Random(8152026)
    road, wind = array("h"), array("h")
    slow = medium = previous = 0.0
    slow_alpha = 1.0 - exp(-2 * pi * 450 / sample_rate)
    medium_alpha = 1.0 - exp(-2 * pi * 2200 / sample_rate)
    for i in range(count):
        white = random.uniform(-1.0, 1.0)
        slow += (white - slow) * slow_alpha
        medium += (white - medium) * medium_alpha
        road_value = 0.7 * slow + 0.3 * medium
        wind_value = 0.6 * medium + 0.1 * (white - previous)
        previous = white
        road.extend((round(27000 * envelope[i] * road_value),) * channels)
        wind.extend((round(22000 * envelope[i] * wind_value),) * channels)
    clips.extend((road.tobytes(), wind.tobytes()))
    return AudioBank(sample_rate, channels, tuple(clips))


def voice_mix(
    parameters: SoundParameters, elapsed: float = 0.0
) -> tuple[tuple[int, float], ...]:
    rpm = clamp(parameters.rpm, RPM_ANCHORS[0], RPM_ANCHORS[-1])
    lower = 0
    while lower < len(RPM_ANCHORS) - 2 and rpm > RPM_ANCHORS[lower + 1]:
        lower += 1
    fraction = (rpm - RPM_ANCHORS[lower]) / (
        RPM_ANCHORS[lower + 1] - RPM_ANCHORS[lower]
    )
    load = clamp(parameters.engine_load)
    gain = clamp(parameters.engine_volume)

    def asset(anchor: int, heavy: int) -> int:
        # Nearest precomputed oscillator phase, <=22.5 degrees fundamental error.
        phase = (
            round((elapsed * RPM_ANCHORS[anchor] / 30.0 % 1.0) * PHASE_COUNT)
            % PHASE_COUNT
        )
        return (2 * anchor + heavy) * PHASE_COUNT + phase

    return (
        (asset(lower, 0), gain * (1.0 - fraction) * (1.0 - load)),
        (asset(lower, 1), gain * (1.0 - fraction) * load),
        (asset(lower + 1, 0), gain * fraction * (1.0 - load)),
        (asset(lower + 1, 1), gain * fraction * load),
        (NOISE_INDEX, clamp(parameters.road_gain)),
        (NOISE_INDEX + 1, clamp(parameters.wind_gain)),
    )
