from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import pairwise
from math import isfinite, sqrt
from typing import Final

UNIT_SECONDS: Final = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}
MIN_FIT_SPAN_SECONDS: Final = 1.0  # Numerical conditioning floor, not a research acceptance threshold.
type ClockValue = str | int | float | bool | None | list[ClockValue] | dict[str, ClockValue]


@dataclass(frozen=True, slots=True)
class SessionOrigin:
    monotonic_ns: int
    utc_ns: int


@dataclass(frozen=True, slots=True)
class ClockPair:
    source_value: int | float
    host_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class ClockProvenance:
    source_unit: str
    pair_count: int
    rate_host_ns_per_source_unit: float | None
    offset_host_ns: float | None
    drift_ppm: float | None
    residual_rms_ns: float | None
    residual_min_ns: float | None
    residual_max_ns: float | None
    reason: str | None
    pairs: tuple[ClockPair, ...]
    strategy: str = "UNVERIFIED"
    timing_quality_status: str = "NOT_VALIDATED"
    source_clock: str = "SOURCE_CLOCK_ARBITRARY_ORIGIN"
    target_clock: str = "python.time.monotonic_ns"
    target_resolution_ns: float | None = None
    source_span_s: float | None = None
    minimum_fit_span_s: float = MIN_FIT_SPAN_SECONDS
    anchor_adjustment_ns: float | None = None

    def as_record(self) -> dict[str, ClockValue]:
        record = asdict(self)
        record["pairs"] = [
            {key: value if isfinite(value) else repr(value) for key, value in asdict(pair).items()}
            for pair in self.pairs
        ]
        record["nonfinite_raw_encoding"] = "repr strings; original pairs retained in memory/raw input"
        return record


@dataclass(frozen=True, slots=True)
class AffineClockMapping:
    source_unit: str
    origin: SessionOrigin
    rate: float | None
    offset: float | None
    provenance: ClockProvenance

    @property
    def fitted(self) -> bool:
        return self.rate is not None and self.offset is not None and isfinite(self.rate) and isfinite(self.offset)

    @property
    def verified(self) -> bool:
        return self.fitted and self.provenance.timing_quality_status == "VALIDATED"

    @classmethod
    def unverified(
        cls, source_unit: str, origin: SessionOrigin, reason: str = "UNVERIFIED", *,
        pairs: tuple[ClockPair, ...] = (), target_resolution_ns: float | None = None,
    ) -> AffineClockMapping:
        span = None
        if pairs and source_unit in UNIT_SECONDS and isfinite(pairs[0].source_value) and isfinite(pairs[-1].source_value):
            candidate = (pairs[-1].source_value - pairs[0].source_value) * UNIT_SECONDS[source_unit]
            span = candidate if isfinite(candidate) else None
        return cls(
            source_unit,
            origin,
            None,
            None,
            ClockProvenance(
                source_unit, len(pairs), None, None, None, None, None, None, reason, pairs,
                target_resolution_ns=target_resolution_ns,
                source_span_s=span,
                minimum_fit_span_s=max(MIN_FIT_SPAN_SECONDS, 100 * (target_resolution_ns or 0) / 1e9),
            ),
        )

    @classmethod
    def fit(
        cls, source_unit: str, pairs: tuple[ClockPair, ...], origin: SessionOrigin, *,
        target_resolution_ns: float | None = None,
    ) -> AffineClockMapping:
        def reject(reason: str) -> AffineClockMapping:
            return cls.unverified(source_unit, origin, reason, pairs=pairs,
                                  target_resolution_ns=target_resolution_ns if target_resolution_ns is None or isfinite(target_resolution_ns) else None)

        if source_unit not in UNIT_SECONDS:
            return reject("UNKNOWN_SOURCE_UNIT")
        if len(pairs) < 5:
            return reject("INSUFFICIENT_PAIRS")
        if not all(isfinite(value) for pair in pairs for value in (pair.source_value, pair.host_monotonic_ns)):
            return reject("NONFINITE_PAIR")
        if target_resolution_ns is not None and (not isfinite(target_resolution_ns) or target_resolution_ns <= 0):
            return reject("INVALID_CLOCK_RESOLUTION")
        source = tuple(float(pair.source_value - pairs[0].source_value) for pair in pairs)
        host = tuple(float(pair.host_monotonic_ns - pairs[0].host_monotonic_ns) for pair in pairs)
        if not all(isfinite(value) for value in (*source, *host)):
            return reject("DEGENERATE_RELATIVE_RANGE")
        if any(right <= left for left, right in pairwise(source)):
            return reject("SOURCE_NOT_STRICTLY_INCREASING")
        if any(right <= left for left, right in pairwise(host)):
            return reject("HOST_NOT_STRICTLY_INCREASING")
        minimum_span = max(MIN_FIT_SPAN_SECONDS, 100 * (target_resolution_ns or 0) / 1e9)
        span = source[-1] * UNIT_SECONDS[source_unit]
        if span < minimum_span or host[-1] / 1e9 < minimum_span:
            return reject("INSUFFICIENT_CLOCK_SPAN")
        source_mean = sum(source) / len(source)
        host_mean = sum(host) / len(host)
        if not isfinite(source_mean) or not isfinite(host_mean):
            return reject("DEGENERATE_RELATIVE_RANGE")
        denominator = sum((value - source_mean) * (value - source_mean) for value in source)
        if not isfinite(denominator) or denominator <= 0:
            return reject("DEGENERATE_SOURCE_SPAN")
        rate = (
            sum(
                (source_value - source_mean) * (host_value - host_mean)
                for source_value, host_value in zip(source, host, strict=True)
            )
            / denominator
        )
        if not isfinite(rate) or rate <= 0:
            return reject("NONFINITE_OR_NONPOSITIVE_RATE")
        nominal_rate = UNIT_SECONDS[source_unit] * 1_000_000_000.0
        if not 0.5 <= rate / nominal_rate <= 2:
            return reject("RATE_UNIT_SCALE_MISMATCH")
        adjustment = host_mean - rate * source_mean
        offset = pairs[0].host_monotonic_ns + adjustment - rate * pairs[0].source_value
        residuals = tuple(
            host_value - (adjustment + rate * source_value)
            for source_value, host_value in zip(source, host, strict=True)
        )
        if not all(isfinite(value) for value in (offset, adjustment, *residuals)):
            return reject("NONFINITE_FIT")
        provenance = ClockProvenance(
            source_unit=source_unit,
            pair_count=len(pairs),
            rate_host_ns_per_source_unit=rate,
            offset_host_ns=offset,
            drift_ppm=(rate / nominal_rate - 1.0) * 1_000_000.0,
            residual_rms_ns=sqrt(
                sum(value * value for value in residuals) / len(residuals)
            ),
            residual_min_ns=min(residuals),
            residual_max_ns=max(residuals),
            reason="TIMING_QUALITY_NOT_VALIDATED",
            pairs=pairs,
            strategy="BOUNDED_AFFINE_ESTIMATE_NO_EXTRAPOLATION",
            target_resolution_ns=target_resolution_ns, source_span_s=span,
            minimum_fit_span_s=minimum_span, anchor_adjustment_ns=adjustment,
        )
        return cls(source_unit, origin, rate, offset, provenance)

    def to_session_seconds(self, source_value: float) -> float | None:
        return self.estimate_session_seconds(source_value) if self.verified else None

    def estimate_session_seconds(self, source_value: float) -> float | None:
        if not self.fitted or not isfinite(source_value) or not self.provenance.pairs:
            return None
        first, last = self.provenance.pairs[0], self.provenance.pairs[-1]
        if not first.source_value <= source_value <= last.source_value:
            return None
        relative_ns = ((first.host_monotonic_ns - self.origin.monotonic_ns)
                       + self.provenance.anchor_adjustment_ns
                       + self.rate * (source_value - first.source_value))
        return relative_ns / 1e9 if isfinite(relative_ns) else None
