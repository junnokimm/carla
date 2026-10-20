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
    if any(p[2] < 0 for p in pairs):
        return {**result, "status": "INVALID_BRACKET"}
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


def validated_clock_fit(pairs):
    """Five independent check points spanning the capture; never refit on them."""
    all_pairs = fit_clock(pairs)
    if len(pairs) < 10 or all_pairs["status"] in (
        "NONFINITE_PAIR",
        "CLOCK_RESET_OR_NONMONOTONIC",
        "INSUFFICIENT_SPAN",
        "INVALID_BRACKET",
    ):
        return {
            **all_pairs,
            "status": "UNVERIFIED",
            "holdout_status": "INSUFFICIENT_OR_INVALID_PAIRS",
        }
    indices = {1 + round(i * (len(pairs) - 3) / 4) for i in range(5)}
    training = [p for i, p in enumerate(pairs) if i not in indices]
    fit = fit_clock(training)
    checks = []
    for i in sorted(indices):
        x, y, width = pairs[i]
        predicted = map_time(fit, x)
        checks.append(
            {
                "pair_index": i,
                "source_s": x,
                "observed_target_s": y,
                "predicted_target_s": predicted,
                "pair_half_width_s": width,
                "error_s": None if predicted is None else predicted - y,
            }
        )
    passed = all(
        c["error_s"] is not None
        and abs(c["error_s"]) <= 0.010
        and c["pair_half_width_s"] <= 0.020
        for c in checks
    )
    return {
        **fit,
        "status": "SOFTWARE_ALIGNMENT_CHECKED" if passed else "HOLDOUT_CHECK_FAILED",
        "total_pair_count": len(pairs),
        "training_pair_count": len(training),
        "holdout_count": len(checks),
        "holdout_status": "PASS" if passed else "FAIL",
        "holdout_max_error_s": max(
            (abs(c["error_s"]) for c in checks if c["error_s"] is not None),
            default=None,
        ),
        "holdout_checks": checks,
        "holdout_error_change_s": (
            checks[-1]["error_s"] - checks[0]["error_s"] if passed else None
        ),
        "validation_policy": "FIVE_SPREAD_INTERIOR_PAIRS_EXCLUDED_FROM_FIT;ENDPOINTS_RETAINED;NO_EXTRAPOLATION",
    }


def tobii_alignment(records, origin_ns, *, origin_perf_counter_ns=None):
    """New runs use SDK -> shared QPC; legacy runs retain the coarse parent bridge."""
    sdk, parent = [], []
    invalid_sdk = invalid_parent = pair_errors = 0
    for r in records:
        pair_errors += r.get("record_type") == "clock_pair_error"
        if r.get("record_type") == "clock_pair" and "host_perf_counter_before_ns" in r:
            try:
                a, b = r["host_perf_counter_before_ns"], r["host_perf_counter_after_ns"]
                if not b >= a:
                    raise ValueError("invalid bracket")
                sdk.append((r["sdk_system_us"] / 1e6, (a + b) / 2e9, (b - a) / 2e9))
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid_sdk += 1
        if r.get("record_type") == "parent_clock_bridge":
            try:
                a, b = r["host_before_ns"], r["host_after_ns"]
                if not b >= a:
                    raise ValueError("invalid bracket")
                parent.append(
                    (
                        r["perf_counter_ns"] / 1e9,
                        ((a + b) / 2 - origin_ns) / 1e9,
                        (b - a) / 2e9,
                    )
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid_parent += 1
    first, second = fit_clock(sdk), fit_clock(parent)
    checked = validated_clock_fit(sdk)
    if invalid_sdk:
        first["status"] = checked["status"] = "INVALID_CLOCK_PAIR"
    if invalid_parent:
        second["status"] = "INVALID_CLOCK_PAIR"
    evidence = {
        "invalid_sdk_pairs": invalid_sdk,
        "invalid_parent_pairs": invalid_parent,
        "clock_pair_error_records": pair_errors,
    }
    if origin_perf_counter_ns is not None:
        return {
            "sdk_to_shared_qpc": checked,
            "shared_qpc_to_session": second,
            "origin_perf_counter_ns": origin_perf_counter_ns,
            "status": checked["status"],
            "source": "TOBII_SYSTEM_US",
            "session_clock": "SHARED_QPC",
            "physical_sync_validated": False,
            "legacy_parent_bridge_used": False,
            "source_evidence": evidence,
        }
    return {
        "sdk_to_shared_qpc": first,
        "shared_qpc_to_session": second,
        "status": "SOFTWARE_ALIGNMENT_CHECKED"
        if all(f["status"] == "SOFTWARE_ALIGNMENT_CHECKED" for f in (first, second))
        else "UNVERIFIED",
        "source": "TOBII_SYSTEM_US",
        "session_clock": "PARENT_MONOTONIC",
        "physical_sync_validated": False,
        "sdk_to_shared_qpc_holdout": checked,
        "source_evidence": evidence,
    }


def map_tobii(alignment, system_us):
    qpc = map_time(alignment["sdk_to_shared_qpc"], system_us / 1e6)
    if alignment.get("session_clock") == "SHARED_QPC":
        return None if qpc is None else qpc - alignment["origin_perf_counter_ns"] / 1e9
    return map_time(alignment["shared_qpc_to_session"], qpc)


def five_markers(anchors):
    """Retain five actual simulator anchors, never invent missing observations."""
    if len(anchors) < 5:
        return []
    return [
        {"marker_id": f"SYNC-{i + 1}", **anchors[round(i * (len(anchors) - 1) / 4)]}
        for i in range(5)
    ]
