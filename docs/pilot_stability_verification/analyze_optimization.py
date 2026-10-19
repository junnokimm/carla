"""Print reproducible offline optimization evidence; never connect to CARLA.

Times below are a deterministic RPC-cost model, NOT measured live FPS.
Run with the pilot Python interpreter and -B after optimization_tests.xml exists.
"""

import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import carla
import pygame
import pytest

from tests.test_manual_loop_optimization import run_probe
from tests.test_p5_exit_assistance import ACTIVE, MANUAL


def forbidden(*args, **kwargs):
    raise AssertionError("Offline evidence forbids CARLA and physical joystick")


carla.Client = forbidden
pygame.joystick.Joystick = forbidden
directory = Path(__file__).resolve().parent
baseline = json.loads((directory / "nonfatal_runtime_summary.json").read_text())
test_tree = ET.parse(directory / "optimization_tests.xml")
cases = test_tree.findall(".//testcase")
failures = sorted(
    [case.attrib["classname"], case.attrib["name"]]
    for case in cases
    if case.find("failure") is not None or case.find("error") is not None
)
skipped = sum(case.find("skipped") is not None for case in cases)
benchmarks = {}
for state in (MANUAL, ACTIVE):
    pair = []
    for optimized in (False, True):
        with pytest.MonkeyPatch.context() as patch:
            result = run_probe(patch, optimized=optimized, state=state)
        pair.append(result)
    before, after = pair
    preserved = {
        key: before[key] == after[key]
        for key in ("controls", "observations", "audio", "events")
    }
    assert all(preserved.values())
    benchmarks[state.control_mode.value] = {
        "loops": 60,
        "before_counts": before["counts"],
        "after_counts": after["counts"],
        "before_modeled_loop_ms": before["loop_mean_ms"],
        "after_modeled_loop_ms": after["loop_mean_ms"],
        "preserved": preserved,
        "timestamps": "Fresh capture timestamps retained; higher cadence naturally changes sample times",
    }

run = "P8-PILOT-900b52901668434c9ff416e0866a1961"
original_hashes = {
    f"research_events_{run}.csv": "0ba9e686f69e65ea5cf90f0f6ba659ba6ec7bfadf7d58fb0ee8439867c5da646",
    f"research_telemetry_{run}.csv": "bd6ffef8a370cf701a057f12b18767b294992a9601d2548730d7747991b8bc45",
}
hashes = {}
for name, expected in original_hashes.items():
    with (ROOT / "data" / "p8_operator" / run / name).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    hashes[name] = {"sha256": actual, "matches_original": actual == expected}

print(json.dumps({
    "scope": "MANUAL loop repeated reads and event-stall diagnostics only",
    "passed": len(cases) - len(failures) - skipped,
    "failed": len(failures),
    "skipped": skipped,
    "new_failures": [item for item in failures if item not in baseline["existing_failures"]],
    "existing_failures": failures,
    "blocking_cost_model": {
        "warning": "SIMULATED_COSTS_NOT_LIVE_FPS_OR_WALL_CLOCK_BENCHMARK",
        "cost_ms": {"bounding_box": 21.7, "light_query": 21.6, "checked_control": 18.2, "draw": 4, "flip": 0.6},
        "manual_input": "60 samples; alternating +/-0.2 steering; throttle 0.6; brake 0.3 every fifth loop",
        "modes": benchmarks,
    },
    "invariants": {
        "checked_control_acknowledgment": "UNCHANGED; every manual loop; no worker, queue, or skipped delivery",
        "activation": "Fresh geometry each check; fresh bounding box on actual activation",
        "telemetry": "Every existing capture still queries fresh CARLA state and keeps its timestamps",
        "audio": "Same-loop observed light state only, max age 50ms; original fresh RPC fallback",
        "hazard_and_recovery": "Algorithms unchanged; exercised by existing full offline regression suite",
    },
    "stall_diagnostics": ["event_poll", "event_poll_thread_cpu", "presentation_events", "presentation_events_thread_cpu", "fullscreen_transition"],
    "stall_diagnostics_location": "performance_summary.camera.timing_by_mode and camera.slowest_completed_loop.segments",
    "original_pilot_hashes": hashes,
    "live_carla_or_device_test": False,
    "remaining_validation": "User-run same-condition NoA/MANUAL comparison; actual display cadence, input response, hazard exposure, and export",
}, indent=2))
