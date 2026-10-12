import math

import pytest

from src.p6.clock import AffineClockMapping, ClockPair, SessionOrigin


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_pairs_cannot_be_verified(bad):
    pairs = tuple(ClockPair(bad if i == 2 else i * 1_000_000, i * 1_000_000_000) for i in range(5))

    mapping = AffineClockMapping.fit("us", pairs, SessionOrigin(0, 0))

    assert not mapping.verified
    assert mapping.rate is None and mapping.offset is None
    assert mapping.provenance.reason == "NONFINITE_PAIR"
    assert mapping.provenance.pairs == pairs


def test_short_startup_span_is_not_extrapolated_over_recording():
    pairs = tuple(ClockPair(i * 15_864, i * 15_625_000) for i in range(5))

    mapping = AffineClockMapping.fit("us", pairs, SessionOrigin(0, 0))

    assert mapping.to_session_seconds(30_000_000) is None
    assert mapping.provenance.reason == "INSUFFICIENT_CLOCK_SPAN"
    assert not mapping.fitted


@pytest.mark.parametrize("drift_ppm", [-100, 0, 100])
def test_full_span_fit_is_numeric_evidence_not_timing_approval(drift_ppm):
    origin = SessionOrigin(1_000_000_000, 0)
    rate = 1000 * (1 + drift_ppm / 1e6)
    pairs = tuple(ClockPair(i * 2_000_000, round(origin.monotonic_ns + i * 2_000_000 * rate)) for i in range(6))

    mapping = AffineClockMapping.fit("us", pairs, origin)

    assert mapping.fitted
    assert not mapping.verified
    assert mapping.provenance.timing_quality_status == "NOT_VALIDATED"
    assert mapping.provenance.drift_ppm == pytest.approx(drift_ppm)
    assert mapping.estimate_session_seconds(4_000_000) == pytest.approx(4 * (1 + drift_ppm / 1e6))
    assert mapping.estimate_session_seconds(-1) is None
    assert mapping.estimate_session_seconds(10_000_001) is None
    assert mapping.to_session_seconds(4_000_000) is None


def test_quantized_target_clock_records_resolution_and_never_approves_quality():
    quantum = 15_625_000
    pairs = tuple(ClockPair(i * 1_100_000, round(i * 1_100_000_000 / quantum) * quantum) for i in range(10))

    mapping = AffineClockMapping.fit("us", pairs, SessionOrigin(0, 0), target_resolution_ns=quantum)

    assert mapping.fitted and not mapping.verified
    assert mapping.provenance.target_resolution_ns == quantum
    assert mapping.provenance.residual_rms_ns > 0
    assert math.isfinite(mapping.rate)


def test_units_or_degenerate_rate_are_rejected():
    pairs = tuple(ClockPair(i * 1_000_000, i * 10_000_000_000) for i in range(5))
    mapping = AffineClockMapping.fit("us", pairs, SessionOrigin(0, 0))
    assert not mapping.fitted
    assert mapping.provenance.reason == "RATE_UNIT_SCALE_MISMATCH"
