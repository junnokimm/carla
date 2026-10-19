"""Build a provisional, partial tablet AOI from labelled recheck captures (offline)."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from src.p6.external_tablet import ExternalTablet, convex_hull

TARGETS = ("tablet_center", "tablet_top_left", "tablet_top_right", "tablet_bottom_left", "tablet_bottom_right")


def quantile(values, fraction):
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def build(directory):
    directory = Path(directory)
    display_path = directory / "display_area.json"
    display = json.loads(display_path.read_text(encoding="utf-8-sig"))
    if display["status"] != "DISPLAY_AREA_READ":
        raise ValueError("DISPLAY_AREA_REQUIRED")
    corners = {key: display[key] for key in ("top_left", "top_right", "bottom_left", "bottom_right")}
    vertices = {eye: [] for eye in ("left", "right")}
    sources, evidence = [], {}
    for target in TARGETS:
        path = directory / "tablet" / f"{target}.jsonl"
        raw_bytes = path.read_bytes()
        rows = [json.loads(line) for line in raw_bytes.decode("utf-8-sig").splitlines() if line.strip()]
        gazes = [row for row in rows if row.get("record_type") == "gaze"]
        if not gazes or not any(row.get("status") == "RAW_CAPTURED" and row.get("dropped_count") == 0 for row in rows):
            raise ValueError(f"INCOMPLETE_CAPTURE:{target}")
        if any(row.get("synthetic") is not False or row["device"]["serial_number"] != display["serial_number"] for row in gazes):
            raise ValueError("CAPTURE_IDENTITY_MISMATCH")
        first = json.loads(gazes[0]["raw_sdk_json"])["device_time_stamp"]
        last = json.loads(gazes[-1]["raw_sdk_json"])["device_time_stamp"]
        if last - first < 8_000_000:
            raise ValueError("CAPTURE_TOO_SHORT")
        points = {eye: [] for eye in vertices}
        for row in gazes:
            raw = json.loads(row["raw_sdk_json"])
            if not first + 2_000_000 <= raw["device_time_stamp"] <= last - 2_000_000:
                continue
            for eye, values in points.items():
                point = raw[f"{eye}_gaze_point_on_display_area"]
                if raw[f"{eye}_gaze_point_validity"] == 1 and all(math.isfinite(v) for v in point):
                    values.append(point)
        evidence[target] = {}
        for eye, samples in points.items():
            entry = {"sdk_valid_samples_after_trim": len(samples), "included": len(samples) >= 60}
            if entry["included"]:
                x, y = zip(*samples)
                # Only a small noise margin around measured directions, no missing-corner extrapolation.
                x0, x1 = quantile(x, .01) - .01, quantile(x, .99) + .01
                y0, y1 = quantile(y, .01) - .01, quantile(y, .99) + .01
                box = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                vertices[eye].extend(box)
                entry["envelope"] = box
            evidence[target][eye] = entry
        sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(raw_bytes).hexdigest()})
    profile = {
        "schema_version": 1, "profile_id": f"external-tablet-{directory.name}",
        "method": "PER_EYE_OBSERVED_CONVEX_HULL",
        "coordinate_system": "EXTENDED_NORMALIZED_TOBII_DISPLAY_PLANE_NOT_PHYSICAL_TABLET",
        "status": "PROVISIONAL_PARTIAL_EMPIRICAL_MAPPING", "independently_validated": False,
        "serial_number": display["serial_number"], "display_area": corners,
        "polygons": {eye: convex_hull(points) for eye, points in vertices.items()},
        "missing_targets": [target for target, eyes in evidence.items() if not any(e["included"] for e in eyes.values())],
        "fit": {"trim_each_end_seconds": 2, "min_samples_per_eye_target": 60,
                "coordinate_quantiles": [.01, .99], "noise_margin_normalized": .01},
        "target_evidence": evidence, "sources": sources,
        "display_source_sha256": hashlib.sha256(display_path.read_bytes()).hexdigest(),
        "reuse_requirement": "UNCHANGED_NANO_MONITOR_TABLET; PERSONAL_ETM_CALIBRATION_AND_SHORT_AOI_CHECK",
        "limitations": ["Unobserved corners are not extrapolated.",
                        "Hull interiors interpolate measured directions; accuracy needs independent checks.",
                        "Single-eye matches are retained separately; invalid gaze is never imputed.",
                        "This is not a measured physical tablet plane or validated fixation detector."]}
    ExternalTablet(profile)
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    profile = build(args.capture_directory)
    # Never silently replace an approved equipment profile.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"profile": str(args.output), "status": profile["status"],
                      "missing_targets": profile["missing_targets"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
