# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run separately with .venv and .venv-tobii: python -m tools.tobii.clock_diagnostics
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tools.tobii.clock_evidence import clock_metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded local clock diagnostics only; no Tobii SDK or CARLA connection")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = clock_metadata()
    for name, function in (("monotonic", time.monotonic_ns), ("perf_counter", time.perf_counter_ns)):
        deadline = time.perf_counter() + 0.2
        previous = function()
        count = zeros = 0
        smallest = None
        while time.perf_counter() < deadline and count < 1_000_000:
            current = function()
            delta = current - previous
            zeros += delta == 0
            if delta > 0:
                smallest = delta if smallest is None else min(smallest, delta)
            previous = current
            count += 1
        result["clocks"][name].update({"observed_min_positive_delta_ns": smallest,
                                       "zero_delta_count": zeros, "read_count": count,
                                       "observation_semantics": "empirical read increments, not absolute accuracy"})
    result["bridge_samples"] = []
    for index in range(6):
        before = time.monotonic_ns()
        perf = time.perf_counter_ns()
        after = time.monotonic_ns()
        result["bridge_samples"].append({"index": index, "monotonic_before_ns": before,
                                         "perf_counter_ns": perf, "monotonic_after_ns": after})
    result["cross_process_origin_validated"] = False
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
