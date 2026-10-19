"""Prepare the TS-XW input-only SDL policy, then run the unchanged pilot entry."""

from __future__ import annotations

import json

from src.p8.noa_tm import enable_lane_change_runtime
from src.p8.tsxw_sdl import preserve_existing_resistance


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(preserve_existing_resistance()), flush=True)
    print(json.dumps(enable_lane_change_runtime()), flush=True)
    # Import only after preloading SDL: operator_cli imports pygame transitively.
    from tools.run_pilot import main as run_pilot

    return run_pilot(argv)


if __name__ == "__main__":
    raise SystemExit(main())
