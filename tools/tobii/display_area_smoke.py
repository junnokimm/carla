"""Read the saved Nano display geometry in the isolated Python 3.10 SDK runtime."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

from tools.tobii.sdk import DeviceIdentity, TobiiError, load_sdk


def read_display_area(serial: str | None = None) -> dict:
    sdk = load_sdk()
    trackers = [t for t in sdk.find_all_eyetrackers() if DeviceIdentity.from_tracker(t).is_nano
                and (serial is None or t.serial_number == serial)]
    if len(trackers) != 1:
        raise TobiiError("NO_DEVICE" if not trackers else "MULTIPLE_NANOS",
                         "Connect one Nano or supply --serial.")
    tracker = trackers[0]
    area = tracker.get_display_area()
    return {
        "status": "DISPLAY_AREA_READ", "observed_utc": datetime.now(timezone.utc).isoformat(),
        "serial_number": tracker.serial_number, "model": tracker.model,
        "width_mm": area.width, "height_mm": area.height,
        "top_left": area.top_left, "top_right": area.top_right,
        "bottom_left": area.bottom_left, "bottom_right": area.bottom_right,
        "calibration_verified": False, "physical_geometry_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial")
    args = parser.parse_args(argv)
    try:
        report = read_display_area(args.serial)
    except Exception as error:
        # SDK native failures are reported to the bounded parent; no device setting is written.
        logging.getLogger(__name__).exception("Tobii display read failed")
        print(json.dumps({"status": "DISPLAY_AREA_READ_FAILED", "detail": str(error)}))
        return 2
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
