from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from src.p6.aoi import AoiLayout
from src.p6.clock import SessionOrigin
from src.p6.models import RawGazeSample
from src.p6.validation.analysis import analyze
from src.p6.validation.mapping import ValidationError, WindowGeometry
from src.p6.validation.recording import RawRecorder
from src.p6.validation.segments import SegmentEvent, SegmentSequence
from src.p6.validation.summary import Json


class ValidationRun:
    def __init__(self, directory: Path, layout: AoiLayout) -> None:
        directory.mkdir(parents=True, exist_ok=False)
        self.directory, self.layout = directory, layout
        self.origin = SessionOrigin(time.monotonic_ns(), time.time_ns())
        self._events = (directory / "events.jsonl").open("x", encoding="utf-8")
        self.recorder = RawRecorder(directory / "raw_gaze.jsonl", self.origin)
        self.sequence = SegmentSequence(directory.name, self.origin.monotonic_ns, self._event)
        self.sequence.prepare(self.origin.monotonic_ns)
        self.geometry: WindowGeometry | None = None
        self.last_geometry: WindowGeometry | None = None
        self.operator_confirmed = False
        self.confirmed_at_ns: int | None = None
        self.last_sample_ns: int | None = None
        self.sample_count = 0
        self.errors: list[str] = []

    def sample(self, sample: RawGazeSample) -> None:
        if sample.source_metadata.get("synthetic") is not False:
            raise ValidationError("Synthetic samples are forbidden in live AOI validation")
        self.last_sample_ns = time.monotonic_ns()
        self.sample_count += 1

    def confirm(self, geometry: WindowGeometry, now_ns: int) -> None:
        self.geometry = geometry
        self.operator_confirmed = True
        self.confirmed_at_ns = now_ns
        self._event(SegmentEvent(self.directory.name, "OPERATOR_DISPLAY_CONFIRM", 0, "FORWARD",
                                 now_ns, (now_ns - self.origin.monotonic_ns) / 1e9,
                                 geometry.monitor_name))

    def finish(self) -> dict[str, Json]:
        self._events.close()
        self.recorder.close()
        if self.recorder.error:
            self.errors.append(self.recorder.error)
        observed = self.geometry or self.last_geometry
        report = analyze(self.directory, self.layout, self.sequence, geometry=observed,
                         operator_confirmed=self.operator_confirmed)
        report["sequence_status"] = self.sequence.phase
        if self.errors:
            report["status"] = "RUNTIME_ERROR"
        report["runtime_errors"] = self.errors
        report["log_dropped_records"] = self.recorder.dropped
        evidence = {
            "validation_run_id": self.directory.name,
            "timestamp_utc_ns": time.time_ns(),
            "window_geometry": None if observed is None else asdict(observed),
            "last_observed_geometry": None if self.last_geometry is None else asdict(self.last_geometry),
            "display_mapping": None if observed is None else asdict(observed.display_mapping),
            "candidate_aoi_version": self.layout.version,
            "mirror_rectangles": {name: asdict(rect) for name, rect in (
                ("rear", self.layout.rear), ("left", self.layout.left), ("right", self.layout.right))},
            "forward_polygon": asdict(self.layout.forward),
            "device": report["device"], "calibration_status": "NOT_VERIFIED",
            "operator_etm_display_confirmed": self.operator_confirmed,
            "operator_confirmation_monotonic_ns": self.confirmed_at_ns,
            "dpi_coordinate_context": "PER_MONITOR_AWARE_V2_THREAD",
            "display_mapping_verified": False, "client_mapping_verified": False,
            "aoi_validated": False, "hardware_validated": False, "tablet_status": "NOT_TESTED",
        }
        with (self.directory / "mapping_evidence.json").open("x", encoding="utf-8") as output:
            json.dump(evidence, output, indent=2, allow_nan=False)
        with (self.directory / "summary.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
        return report

    def _event(self, event: SegmentEvent) -> None:
        self._events.write(json.dumps(asdict(event)) + "\n")
        self._events.flush()
