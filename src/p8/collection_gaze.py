"""M2 sample proportions, separately from conservative observed gaze durations."""

from collections import Counter

from src.p8.collection_clock import map_tobii

AOIS = ("FORWARD", "LEFT_MIRROR", "RIGHT_MIRROR", "TABLET")


def gaze_metrics(samples, *, windows, alignment, eligible, hazard_start=None):
    counts = {name: Counter() for name in ("all_active_m2", "before_hazard")}
    durations = {name: Counter() for name in counts}
    longest_loss = dict.fromkeys(counts, 0.0)
    loss_run = dict.fromkeys(counts, 0.0)
    rejected = Counter()
    previous = None
    aligned = []
    for row in samples:
        if row.get("record_type") != "gaze":
            continue
        sample = row.get("sample", {})
        metadata = sample.get("source_metadata", {})
        system_us = metadata.get("system_timestamp_raw")
        if system_us is None:
            # Decoder preserves the exact SDK wire payload too.
            import json

            raw = metadata.get("raw_sdk_json")
            if raw:
                system_us = json.loads(raw).get("system_time_stamp")
        t = None if system_us is None else map_tobii(alignment, system_us)
        basis = "SDK_ACQUISITION_SOFTWARE_ALIGNED"
        if t is None:
            # New captures have an explicit parent/QPC callback bridge. Old worker
            # monotonic session labels are not silently considered synchronized.
            t = row.get("parent_callback_session_s")
            basis = "CALLBACK_BRIDGE_APPROXIMATE" if t is not None else "UNALIGNED"
        aligned.append(
            {
                "source_sequence": sample.get("source_sequence"),
                "session_elapsed_s": t,
                "time_basis": basis,
                "aoi": row.get("aoi"),
                "observation_state": row.get("observation_state"),
            }
        )
        window_index = next(
            (i for i, (a, b) in enumerate(windows) if t is not None and a <= t < b),
            None,
        )
        if window_index is None:
            rejected["unaligned_or_outside_active_m2"] += 1
            previous = None
            loss_run = dict.fromkeys(counts, 0.0)
            continue
        # SDK validity includes the external tablet outside the display rectangle.
        tracked = bool(row.get("sdk_tracked_eyes"))
        aoi = row.get("aoi", "NO_AOI")
        state = row.get("observation_state", "UNKNOWN")
        cohorts = ["all_active_m2"]
        if hazard_start is None or t < hazard_start:
            cohorts.append("before_hazard")
        seq = sample.get("source_sequence")
        for name in cohorts:
            c = counts[name]
            c["received_samples"] += 1
            c["tracked_samples"] += tracked
            c["tracking_lost_samples"] += not tracked
            c["approximate_timestamp_samples"] += (
                basis != "SDK_ACQUISITION_SOFTWARE_ALIGNED"
            )
            c[aoi] += tracked
            c[state + "_samples"] += 1
            if previous is not None:
                pt, ps, pa, pq, pw = previous
                dt = t - pt
                # No integration through missed packets, long pauses or state changes.
                if (
                    0 < dt <= 0.1
                    and seq is not None
                    and pq is not None
                    and seq == pq + 1
                    and pw == window_index
                    and (name != "before_hazard" or pt < (hazard_start or float("inf")))
                ):
                    key = (
                        aoi
                        if tracked and aoi == pa and state == ps
                        else (
                            "TRACKING_LOST"
                            if state == ps == "TRACKING_LOST"
                            else "TRANSITION_UNCERTAIN"
                        )
                    )
                    durations[name][key] += dt
                    loss_run[name] = (
                        loss_run[name] + dt if key == "TRACKING_LOST" else 0
                    )
                    longest_loss[name] = max(longest_loss[name], loss_run[name])
                else:
                    loss_run[name] = 0
        previous = (t, state, aoi, seq, window_index)
    reports = {}
    for name, c in counts.items():
        n, valid = c["received_samples"], c["tracked_samples"]
        expected = sum(
            max(0, min(b, hazard_start) - a)
            if name == "before_hazard" and hazard_start is not None
            else b - a
            for a, b in windows
        )
        accounted = sum(durations[name].values())
        reports[name] = {
            "status": "COLLECTED" if n else "NO_USABLE_SAMPLES",
            "counts": dict(c),
            "denominator": "EITHER_EYE_SDK_VALID_INCLUDING_OFF_DISPLAY",
            "raw_sample_ratio_by_aoi": {
                a: c[a] / valid if valid else None for a in (*AOIS, "NO_AOI")
            },
            "analysis_sample_ratio_by_aoi": {
                a: c[a] / valid if valid and eligible.get(a) is True else None
                for a in AOIS
            },
            "tracking_loss_sample_ratio": c["tracking_lost_samples"] / n if n else None,
            "observed_seconds_by_state": dict(durations[name]),
            "active_window_s": expected,
            "unobserved_or_gap_seconds": max(0, expected - accounted),
            "unobserved_or_gap_time_ratio": max(0, expected - accounted) / expected
            if expected > 0
            else None,
            "analysis_observed_seconds_by_aoi": {
                a: durations[name][a] if n and eligible.get(a) is True else None
                for a in AOIS
            },
            "longest_contiguous_tracking_loss_s": longest_loss[name] if n else None,
            "true_tablet_look_seconds": None,
            "eligible_aois": eligible,
            "missing_rule": "UNCHECKED_OR_FAILED_AOI_NULL;UNKNOWN_NEVER_TABLET;NO_GAP_IMPUTATION",
        }
    return {
        "cohorts": reports,
        "rejected": dict(rejected),
        "windows": windows,
        "hazard_exclusion_start_s": hazard_start,
        "hazard_exclusion_policy": "PREPARATION_START_TO_M2_END;ALL_ACTIVE_ALSO_REPORTED",
    }, aligned
