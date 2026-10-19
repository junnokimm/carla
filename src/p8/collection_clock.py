"""Offline clock estimates. Never equate independent clock origins or extrapolate."""

from itertools import pairwise
from math import isfinite, sqrt


def fit_clock(pairs, *, nominal_rate=1.0, min_pairs=5, min_span_s=4.0):
    """Pairs are (source seconds, host/session seconds, bracket half width seconds)."""
    result = {
        "status": "INSUFFICIENT_EVIDENCE",
        "pair_count": len(pairs),
        "physical_sync_validated": False,
        "extrapolation": "FORBIDDEN",
        "rate": None,
        "offset_s": None,
        "drift_ppm": None,
    }
    if len(pairs) < min_pairs:
        return result
    if not all(isfinite(v) for p in pairs for v in p):
        return {**result, "status": "NONFINITE_PAIR"}
    if any(b[0] <= a[0] or b[1] <= a[1] for a, b in pairwise(pairs)):
        return {**result, "status": "CLOCK_RESET_OR_NONMONOTONIC"}
    x0, y0, _ = pairs[0]
    xs, ys = [p[0] - x0 for p in pairs], [p[1] - y0 for p in pairs]
    span = xs[-1]
    if span < min_span_s:
        return {**result, "status": "INSUFFICIENT_SPAN", "span_s": span}
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    rate = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / sum(
        (x - xm) ** 2 for x in xs
    )
    intercept = ym - rate * xm
    residuals = [y - (rate * x + intercept) for x, y in zip(xs, ys)]
    maximum = max(abs(r) for r in residuals)
    bracket = max(p[2] for p in pairs)
    drift = (rate / nominal_rate - 1) * 1e6
    checked = rate > 0 and maximum <= 0.010 and bracket <= 0.020 and abs(drift) <= 2000
    return {
        **result,
        "status": "SOFTWARE_ALIGNMENT_CHECKED" if checked else "QUALITY_CHECK_FAILED",
        "rate": rate,
        "offset_s": y0 + intercept - rate * x0,
        "source_anchor_s": x0,
        "target_anchor_s": y0 + intercept,
        "source_start_s": x0,
        "source_end_s": pairs[-1][0],
        "span_s": span,
        "drift_ppm": drift,
        "residual_max_s": maximum,
        "residual_rms_s": sqrt(sum(r * r for r in residuals) / len(residuals)),
        "max_pair_half_width_s": bracket,
        "uncertainty_bound_s": maximum + bracket,
        "quality_rule": "5_PAIRS_SPAN_4S_RESIDUAL_10MS_BRACKET_20MS_DRIFT_2000PPM",
    }


def map_time(fit, value):
    if (
        fit.get("status") != "SOFTWARE_ALIGNMENT_CHECKED"
        or value is None
        or not isfinite(value)
        or not fit["source_start_s"] <= value <= fit["source_end_s"]
    ):
        return None
    return fit["target_anchor_s"] + fit["rate"] * (value - fit["source_anchor_s"])


def tobii_alignment(records, origin_ns):
    """Bridge worker SDK -> Windows QPC -> parent monotonic, not worker monotonic.

    QPC is the OS-wide performance counter on this Windows installation. Parent
    brackets are captured independently; coarse Python 3.10 monotonic is unused.
    """
    sdk, parent = [], []
    for r in records:
        if r.get("record_type") == "clock_pair" and "host_perf_counter_before_ns" in r:
            a, b = r["host_perf_counter_before_ns"], r["host_perf_counter_after_ns"]
            if b >= a:
                sdk.append((r["sdk_system_us"] / 1e6, (a + b) / 2e9, (b - a) / 2e9))
        if r.get("record_type") == "parent_clock_bridge":
            a, b = r["host_before_ns"], r["host_after_ns"]
            if b >= a:
                parent.append(
                    (
                        r["perf_counter_ns"] / 1e9,
                        ((a + b) / 2 - origin_ns) / 1e9,
                        (b - a) / 2e9,
                    )
                )
    first, second = fit_clock(sdk), fit_clock(parent)
    return {
        "sdk_to_shared_qpc": first,
        "shared_qpc_to_session": second,
        "status": "SOFTWARE_ALIGNMENT_CHECKED"
        if all(f["status"] == "SOFTWARE_ALIGNMENT_CHECKED" for f in (first, second))
        else "UNVERIFIED",
        "source": "TOBII_SYSTEM_US",
        "session_clock": "PARENT_MONOTONIC",
        "physical_sync_validated": False,
    }


def map_tobii(alignment, system_us):
    qpc = map_time(alignment["sdk_to_shared_qpc"], system_us / 1e6)
    return map_time(alignment["shared_qpc_to_session"], qpc)


def five_markers(anchors):
    """Retain five actual simulator anchors, never invent missing observations."""
    if len(anchors) < 5:
        return []
    return [
        {"marker_id": f"SYNC-{i + 1}", **anchors[round(i * (len(anchors) - 1) / 4)]}
        for i in range(5)
    ]
