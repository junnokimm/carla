"""Callback-clock AOI durations, never bridging loss, dropped data or transitions."""
from collections import Counter


class GazeDuration:
    def __init__(self, start_ns, emit, *, max_gap_ns=100_000_000):
        self.start_ns = self.edge_ns = start_ns
        self.emit = emit
        self.max_gap_ns = max_gap_ns
        self.previous = None
        self.pending = None
        self.nanoseconds = Counter()
        self.clock_anomalies = 0
        self.clock_gap_pending = False
        self.sample_count = 0

    def _interval(self, end, state, reason):
        start = self.edge_ns
        if end <= start:
            return
        if self.pending and (self.pending["state"], self.pending["reason"]) == (state, reason):
            self.pending["end_clock_ns"] = end
        else:
            self.flush()
            self.pending = {"record_type": "gaze_interval", "state": state, "reason": reason,
                            "start_clock_ns": start, "end_clock_ns": end,
                            "time_basis": "SDK_CALLBACK_PERF_COUNTER_NS_NOT_ACQUISITION"}
        self.nanoseconds[state] += end - start
        self.edge_ns = end
        if end - self.pending["start_clock_ns"] >= 1_000_000_000:
            self.flush()

    def flush(self):
        if self.pending:
            row = self.pending
            row["duration_s"] = (row["end_clock_ns"] - row["start_clock_ns"]) / 1e9
            self.emit(row)
            self.pending = None

    def add(self, time_ns, sequence, device_us, state):
        if time_ns < self.edge_ns or (self.previous is not None and time_ns == self.edge_ns):
            self.clock_anomalies += 1
            self.clock_gap_pending = True
            return
        if self.previous is None:
            self._interval(time_ns, "NO_DATA", "BEFORE_FIRST_SAMPLE")
        else:
            previous_seq, previous_device, previous_state = self.previous
            device_delta = device_us - previous_device
            if (self.clock_gap_pending or sequence != previous_seq + 1
                    or not 0 < device_delta <= self.max_gap_ns / 1000
                    or time_ns - self.edge_ns > self.max_gap_ns):
                self._interval(time_ns, "NO_DATA", "SEQUENCE_CLOCK_OR_CALLBACK_GAP")
            elif state != previous_state:
                self._interval(time_ns, "TRANSITION_UNCERTAIN", "ADJACENT_STATES_DIFFER")
            else:
                self._interval(time_ns, state, "AGREEING_ADJACENT_SAMPLES")
        self.previous = sequence, device_us, state
        self.clock_gap_pending = False
        self.sample_count += 1

    def finish(self, end_ns):
        self._interval(end_ns, "NO_DATA", "AFTER_LAST_SAMPLE_OR_COLLECTION_STOPPED")
        self.flush()

    def summary(self):
        seconds = {state: value / 1e9 for state, value in sorted(self.nanoseconds.items())}
        tablet = seconds.get("TABLET_BINOCULAR", 0) + seconds.get("TABLET_MONOCULAR", 0)
        return {"time_basis": "SDK_CALLBACK_PERF_COUNTER_NS_NOT_ACQUISITION",
                "method": "AGREEING_ADJACENT_SAMPLES_MAX_100MS_NO_INTERPOLATION",
                "observation_start_clock_ns": self.start_ns,
                "observation_end_clock_ns": self.edge_ns,
                "observation_seconds": (self.edge_ns - self.start_ns) / 1e9,
                "seconds_by_state": seconds,
                "tablet_classified_seconds": tablet,
                "tracking_lost_seconds": seconds.get("TRACKING_LOST", 0),
                "no_data_seconds": seconds.get("NO_DATA", 0),
                "transition_uncertain_seconds": seconds.get("TRANSITION_UNCERTAIN", 0),
                "sample_count": self.sample_count, "clock_anomalies": self.clock_anomalies,
                "true_total_tablet_seconds": None,
                "limitation": "CLASSIFIED_OBSERVED_TIME_ONLY; UNKNOWN_TIME_IS_NOT_ZERO_TABLET_TIME"}
