from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from statistics import median

from src.p6.clock import AffineClockMapping, ClockPair, SessionOrigin


@dataclass(frozen=True, slots=True)
class ClockEvidence:
    pairs: tuple[ClockPair, ...]
    phases: tuple[str, ...]
    target_resolution_ns: float | None
    gaze_times: tuple[int, ...]
    errors: tuple[str, ...]
    clock_info: dict
    session_clock_info: dict

    def fit(self, origin: SessionOrigin) -> AffineClockMapping:
        if self.errors:
            return AffineClockMapping.unverified("us", origin, "CLOCK_RECORD_ERROR", pairs=self.pairs)
        return AffineClockMapping.fit("us", self.pairs, origin, target_resolution_ns=self.target_resolution_ns)

    def report(self) -> dict:
        offsets = [pair.host_monotonic_ns - pair.source_value * 1000 for pair in self.pairs]
        group = max(1, len(offsets) // 3)
        start = median(offsets[:group]) if offsets else None
        end = median(offsets[-group:]) if offsets else None
        window_size = max(2, len(self.pairs) // 3)

        def endpoint_drift(window: tuple[ClockPair, ...]) -> float | None:
            if len(window) < 2 or window[-1].source_value <= window[0].source_value:
                return None
            return ((window[-1].host_monotonic_ns - window[0].host_monotonic_ns)
                    / ((window[-1].source_value - window[0].source_value) * 1000) - 1) * 1e6
        coverage = sum(self.pairs[0].source_value <= stamp <= self.pairs[-1].source_value
                       for stamp in self.gaze_times) if self.pairs else 0
        return {"pair_count": len(self.pairs), "phases": list(self.phases),
                "source_clock": "TOBII_SDK_SYSTEM_TIMESTAMP_US_ARBITRARY_ORIGIN",
                "target_clock": "WORKER_PYTHON_MONOTONIC_NS_ARBITRARY_ORIGIN",
                "session_clock": "P4_PYTHON_MONOTONIC_NS_WITH_SESSION_ORIGIN",
                "cross_process_origin_status": "NOT_VALIDATED",
                "pair_window_start_nominal_offset_ns": start, "pair_window_end_nominal_offset_ns": end,
                "pair_window_offset_change_ns": None if start is None or end is None else end - start,
                "pair_window_start_drift_ppm_diagnostic": endpoint_drift(self.pairs[:window_size]),
                "pair_window_end_drift_ppm_diagnostic": endpoint_drift(self.pairs[-window_size:]),
                "recording_start_offset_ns": start if "START" in self.phases else None,
                "recording_end_offset_ns": end if "END" in self.phases else None,
                "recording_end_drift_ppm": endpoint_drift(self.pairs[-window_size:]) if "END" in self.phases else None,
                "pairs_span_s": None if len(self.pairs) < 2 else (self.pairs[-1].source_value - self.pairs[0].source_value) / 1e6,
                "gaze_samples_inside_pair_range": coverage,
                "gaze_samples_outside_pair_range": len(self.gaze_times) - coverage,
                "start_middle_end_collected": all(phase in self.phases for phase in ("START", "MIDDLE", "END")),
                "clock_info": self.clock_info, "session_clock_info": self.session_clock_info,
                "errors": list(self.errors),
                "accuracy_status": "NOT_VALIDATED", "offsets_are_not_latency_corrected": True}


def read_clock_evidence(path: Path) -> ClockEvidence:
    pairs, phases, gaze_times, errors = [], [], [], []
    resolution = None
    info = {}
    session_info = {}
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source):
            try:
                record = json.loads(line)
                kind = record.get("record_type")
                if kind == "clock_pair":
                    sdk, before, after = record["sdk_system_us"], record["host_before_ns"], record["host_after_ns"]
                    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
                           for value in (sdk, before, after)) or after < before:
                        raise ValueError("invalid or nonfinite clock pair")
                    pairs.append(ClockPair(sdk, (before + after) // 2))
                    phases.append(record.get("phase", "LEGACY_UNSPECIFIED"))
                elif kind == "clock_metadata":
                    info = json.loads(record["clock_info_json"])
                    resolution = float(info["clocks"]["monotonic"]["resolution_s"]) * 1e9
                    if not isfinite(resolution) or resolution <= 0:
                        raise ValueError("invalid monotonic resolution")
                elif kind == "session_origin":
                    session_info = json.loads(record.get("session_clock_info_json", "{}"))
                elif kind == "clock_pair_error":
                    errors.append(f"line {index + 1}: clock_pair_error:{record.get('phase')}")
                elif kind == "gaze":
                    raw = json.loads(record["raw_sdk_json"])
                    stamp = raw["system_time_stamp"]
                    if isinstance(stamp, bool) or not isinstance(stamp, int):
                        raise ValueError("invalid raw SDK system timestamp")
                    gaze_times.append(stamp)
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                errors.append(f"line {index + 1}: {error}")
    return ClockEvidence(tuple(pairs), tuple(phases), resolution, tuple(gaze_times), tuple(errors), info, session_info)
