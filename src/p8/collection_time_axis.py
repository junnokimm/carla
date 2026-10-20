"""Analysis-only QPC coordinates. No reconstruction of missing event timestamps."""


def elapsed_ns(value, origin):
    try:
        if value is None or value == "" or origin is None:
            return None
        value, origin = int(value), int(origin)
        return (value - origin) / 1e9 if value >= 0 and origin >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def qpc_windows(events, phase, telemetry_end, manifest):
    windows, start = [], None
    label = f"{phase}_RUNNING"
    failures = []
    for row in events:
        if row.get("phase") != phase:
            continue
        value, kind = row.get("event_value", ""), row.get("event_type")
        is_start = kind == "stage_start" and value == label
        is_end = kind == "stage_end" and value.startswith(label + ":")
        if not (is_start or is_end):
            continue
        t = row.get("analysis_elapsed_s")
        if t is None:
            failures.append("MISSING_MODULE_BOUNDARY_QPC")
        elif is_start:
            start = t
        elif start is not None:
            if t >= start:
                windows.append((start, t))
            else:
                failures.append("NONMONOTONIC_MODULE_BOUNDARY_QPC")
            start = None
    if start is not None and telemetry_end is not None and telemetry_end >= start:
        windows.append((start, telemetry_end))
    origin = manifest["origin"].get("perf_counter_ns")
    for fault in manifest.get("components", {}).get("runtime_faults", []):
        a = elapsed_ns(fault.get("started_perf_counter_ns"), origin)
        b = elapsed_ns(fault.get("completed_perf_counter_ns"), origin)
        if a is None or b is None or b < a:
            failures.append("MISSING_OR_INVALID_PAUSE_QPC")
            continue
        clipped = []
        for left, right in windows:
            if a >= right or b <= left:
                clipped.append((left, right))
            else:
                if left < a:
                    clipped.append((left, a))
                if b < right:
                    clipped.append((b, right))
        windows = clipped
    # Never silently substitute a coarse boundary into a precision-qualified run.
    return ([] if failures else windows), sorted(set(failures))
