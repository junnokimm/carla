"""Provisional coarse-AOI quality rules; independent of driving and calibration."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

AOIS = ("FORWARD", "LEFT_MIRROR", "RIGHT_MIRROR", "TABLET")


@dataclass(frozen=True)
class CheckPolicy:
    version: str = "m2-coarse-aoi-v1-provisional"
    seconds: float = 5.0
    settle_seconds: float = 1.0
    expected_hz: float = 60.0
    min_coverage: float = .90
    min_tracking: float = .85
    min_target: float = .80
    max_wrong: float = .05
    max_loss_seconds: float = .50
    max_attempts: int = 3

    def record(self):
        return asdict(self)


DEFAULT_POLICY = CheckPolicy()


def evaluate_segment(target, rows, start_ns, end_ns, policy=DEFAULT_POLICY):
    """Use callback timestamps, including invalid samples and empty leading/tail gaps."""
    if target not in AOIS or end_ns <= start_ns:
        raise ValueError("INVALID_PRECHECK_SEGMENT")
    rows = [r for r in rows if isinstance(r.get("duration_callback_perf_counter_ns"), int)
            and start_ns <= r["duration_callback_perf_counter_ns"] < end_ns]
    counts = Counter(r["aoi"] for r in rows)
    n = len(rows)
    tracked = sum(bool(r.get("sdk_tracked_eyes")) for r in rows)
    wrong = sum(counts[a] for a in AOIS if a != target)
    duration = (end_ns - start_ns) / 1e9
    coverage = n / (duration * policy.expected_hz)
    tracking = tracked / n if n else 0.0
    correct = counts[target] / n if n else 0.0
    wrong_rate = wrong / n if n else 0.0
    last_valid, max_loss, previous = start_ns, 0, None
    sequence_issues, last_sequence = 0, None
    for row in rows:
        stamp = row["duration_callback_perf_counter_ns"]
        if previous is not None and stamp <= previous:
            sequence_issues += 1
        previous = stamp
        sequence = row.get("sample", {}).get("source_sequence")
        if sequence is not None:
            if last_sequence is not None and sequence <= last_sequence:
                sequence_issues += 1
            last_sequence = sequence
        if row.get("sdk_tracked_eyes") and row.get("observation_state") != "MAPPING_UNAVAILABLE":
            max_loss = max(max_loss, stamp - last_valid)
            last_valid = stamp
    max_loss = max(max_loss, end_ns - last_valid) / 1e9
    reasons = []
    for failed, reason in ((coverage < policy.min_coverage, "INSUFFICIENT_CALLBACK_COVERAGE"),
                           (tracking < policy.min_tracking, "LOW_TRACKING_RATE"),
                           (correct < policy.min_target, "LOW_TARGET_RATE"),
                           (wrong_rate > policy.max_wrong, "AOI_CONFUSION"),
                           (max_loss > policy.max_loss_seconds, "LONG_TRACKING_OR_CALLBACK_LOSS"),
                           (sequence_issues > 0, "CALLBACK_CLOCK_NONMONOTONIC")):
        if failed:
            reasons.append(reason)
    return {"target": target, "passed": not reasons, "reasons": reasons,
            "sample_count": n, "tracked_samples": tracked, "target_samples": counts[target],
            "counts_by_aoi": dict(counts), "coverage": coverage, "tracking_rate": tracking,
            "target_rate": correct, "wrong_aoi_rate": wrong_rate,
            "max_untracked_gap_seconds": max_loss, "clock_issues": sequence_issues,
            "start_perf_ns": start_ns, "end_perf_ns": end_ns,
            "duration_seconds": duration}


def evaluate_attempt(segments, errors=(), policy=DEFAULT_POLICY):
    eligible = {a: False for a in AOIS}
    for aoi in AOIS:
        relevant = [s for s in segments if s["target"] == aoi]
        eligible[aoi] = len(relevant) >= (2 if aoi == "FORWARD" else 1) and all(s["passed"] for s in relevant)
    # A false label also invalidates the destination AOI, even if its own target passed.
    for segment in segments:
        n = segment["sample_count"]
        for aoi in AOIS:
            if aoi != segment["target"] and n and segment["counts_by_aoi"].get(aoi, 0) / n > policy.max_wrong:
                eligible[aoi] = False
    if errors:
        eligible = dict.fromkeys(AOIS, False)
    return {"passed": all(eligible.values()), "eligible_aois": eligible,
            "excluded_aois": [a for a, valid in eligible.items() if not valid],
            "segments": segments, "errors": list(errors), "policy": policy.record(),
            "scope": "M2_START_CENTRAL_TABLET_TASK_ONLY_NOT_WHOLE_TABLET_OR_FULL_RUN",
            "calibration_performed": False}


def stage_quality(stage, decision):
    """None means unvalidated, not passed. Never carry an M2 decision into M1."""
    if stage != "MODULE_2_RUNNING":
        return {"status": "NOT_CHECKED_FOR_THIS_STAGE", "eligible_aois": dict.fromkeys(AOIS),
                "excluded_aois": [], "scope": "NO_PRECHECK_ASSERTION"}
    if not decision:
        return {"status": "M2_PRECHECK_MISSING", "eligible_aois": dict.fromkeys(AOIS, False),
                "excluded_aois": list(AOIS), "scope": "M2"}
    return decision


def analysis_seconds(duration, quality):
    states = duration["seconds_by_state"]
    return {a: ((states.get("TABLET_MONOCULAR", 0) + states.get("TABLET_BINOCULAR", 0))
                if a == "TABLET" else states.get(a, 0))
            if quality.get("eligible_aois", {}).get(a) is True else None for a in AOIS}
