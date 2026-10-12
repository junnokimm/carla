from __future__ import annotations

import json
import random
from dataclasses import asdict, replace
from uuid import uuid4

from src.experiment.context import Module1Condition, Module2Condition
from src.p6.clock import AffineClockMapping, SessionOrigin
from src.p6.source import SyntheticGazeSource
from src.p6.tablet import TabletSession
from src.p6.tablet_models import MediaMetadata, TabletEvent
from src.p6.writer import GazeSidecarWriter
from src.p7.types import Json
from src.p8.model import P8Error
from src.p8.services import SessionServices


class ContextGazeWriter(GazeSidecarWriter):
    def __init__(self, services: SessionServices) -> None:
        self._context = services.context
        origin = SessionOrigin(services.clock.origin.monotonic_ns, services.clock.origin.utc_ns)
        super().__init__(path=services.directory / f"p6_gaze_{self._context.study_run_id}.jsonl",
                         run_id=self._context.study_run_id, origin=origin,
                         mapping=AffineClockMapping.unverified("ns", origin, "DEV_STUB_NOT_CLOCK_VALIDATION"),
                         layout=None)

    def _write(self, record: dict[str, Json]) -> None:
        super()._write({**record, "participant_id": self._context.participant_id,
                        "provenance": "DEV_STUB", "hardware_validation": "NOT_HARDWARE_VALIDATED"})


class DevP6:
    def __init__(self, services: SessionServices) -> None:
        self.services = services
        self.calibration_status = "NOT_TESTED"
        self.source = SyntheticGazeSource(monotonic_ns=services.timeline.monotonic_ns)
        self.gaze: ContextGazeWriter | None = None
        self.sequence = 0
        self._closed = False
        self.tablet_path = services.directory / f"p6_tablet_{services.context.study_run_id}.jsonl"
        self._tablet_file = self.tablet_path.open("x", encoding="utf-8")
        self._tablet_file.write(json.dumps({"record_type": "metadata", **services.envelope(),
                                           "origin_monotonic_ns": services.clock.origin.monotonic_ns,
                                           "origin_utc_ns": services.clock.origin.utc_ns}) + "\n")
        self._tablet_file.flush()
        self.tablet = TabletSession(services.context.study_run_id,
                                    SessionOrigin(services.clock.origin.monotonic_ns, services.clock.origin.utc_ns),
                                    token="DEV_ONLY_" + uuid4().hex, monotonic_ns=services.timeline.monotonic_ns,
                                    event_sink=self._tablet_event, rng=random.Random(7))
        self.media_path = services.directory / "DEV_STUB_NOT_A_VIDEO.bin"
        with self.media_path.open("xb") as fixture:
            fixture.write(b"P8 DEV_STUB: not video, not hardware validation\n")

    def calibrate_stub(self) -> None:
        self.calibration_status = "DEV_STUB_NOT_HARDWARE_VALIDATED"
        if self.gaze is None:
            self.gaze = ContextGazeWriter(self.services)
            self.gaze.start()
        self.services.emit("p6_calibration_boundary", {"status": self.calibration_status, "screen_aoi_ready": False,
                           "tablet_aoi_status": "NOT_TESTED", "clock_sync_ready": False})

    def module_one(self, condition: Module1Condition) -> None:
        self.tablet.configure_module_one(condition)
        if condition is Module1Condition.SURT:
            trial = self.tablet.prepare()
            self.tablet.request_start(trial.trial_uuid)
        self.services.emit("p8_tablet_binding", {"condition": condition.value, "state": self.tablet.state().stage})

    def module_two(self, condition: Module2Condition) -> None:
        self.tablet.configure_module_two(condition, str(self.media_path), "DEV_STUB_NOT_VIDEO")
        trial = self.tablet.prepare()
        self.tablet.request_start(trial.trial_uuid)
        epoch = self.tablet.state().run_epoch
        self.services.emit("p8_video_stub_metadata", {"declared_duration_seconds": 900, "actual_video_file": False})
        self.tablet.record_video_metadata(epoch, trial.trial_uuid, uuid4().hex, MediaMetadata(900, True, False, 1))
        self.tablet.acknowledge(epoch, "media_playing", trial.trial_uuid, uuid4().hex, 0, 0)

    def tick(self) -> None:
        state = self.tablet.state()
        if state.module == "MODULE_1" and state.condition == "SURT" and state.current_trial is not None and state.stage in ("START_REQUESTED", "STIMULUS_SHOWN"):
            self.tablet.acknowledge(state.run_epoch, "shown", state.current_trial.trial_uuid, uuid4().hex, 0, 0)
            self.tablet.touch(state.run_epoch, state.current_trial.trial_uuid, uuid4().hex,
                              state.circles[0].circle_id, 0, 0)
        if self.gaze is not None:
            sample = self.source.emit(self.sequence)
            sample = replace(sample, source_metadata={**sample.source_metadata, **self.services.envelope()})
            self.gaze.capture(sample, receive_monotonic_ns=self.services.timeline.monotonic_ns(),
                              receive_utc_ns=self.services.stamp().host.utc_ns)
            self.sequence += 1

    def stop_tablet(self) -> None:
        self.tablet.end()

    def flush(self) -> None:
        if self._closed:
            return
        try:
            self.tablet.end()
        finally:
            try:
                self._tablet_file.close()
            finally:
                try:
                    if self.gaze is not None:
                        self.gaze.close()
                finally:
                    self._closed = True
        if self.gaze is not None and self.gaze.worker_error is not None:
            raise P8Error(f"P6_WRITER_ERROR:{self.gaze.worker_error}")

    def _tablet_event(self, event: TabletEvent) -> None:
        self._tablet_file.write(json.dumps({"record_type": "tablet_event", **asdict(event), **self.services.envelope()}) + "\n")
        self._tablet_file.flush()
        self.services.emit("p6_tablet_" + event.event_type, asdict(event))
