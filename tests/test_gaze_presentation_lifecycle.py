import io
import json
import time

import pytest

from tests.test_pilot_gaze import Probe, Source, collector, sample


def ready(item):
    item.config = {"display_confirmed": True}
    item.gaze = item.wire = io.StringIO()
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()


def test_queued_sample_after_sdl_shutdown_never_queries_dead_hwnd(tmp_path):
    class DeadWindow(Probe):
        def snapshot(self, *args):
            pytest.fail("GetClientRect must not be called after presentation ended")
    item = collector(tmp_path, probe_factory=DeadWindow)
    ready(item)
    item.end_presentation()
    closed = item.closed_perf_ns
    item._sample(sample())
    row = json.loads(item.gaze.getvalue())
    assert row["aoi"] == "NO_AOI" and not row["duration_eligible"]
    assert row["aoi_reason"] == "PRESENTATION_ENDED"
    assert not item.errors
    item.close()
    assert item.closed_perf_ns == closed


@pytest.mark.parametrize("ending", ["close", "transition", "active_failure"])
def test_window_changes_during_probe_are_not_real_errors_but_live_failure_is(tmp_path, ending):
    class ChangingWindow(Probe):
        def snapshot(self, *args):
            if ending == "close":
                item.end_presentation()
            if ending == "transition":
                item.suspend_presentation()
            raise RuntimeError("GetClientRect failed")
    item = collector(tmp_path, probe_factory=ChangingWindow)
    ready(item)
    item._sample(sample())
    row = json.loads(item.gaze.getvalue())
    assert row["observation_state"] == "MAPPING_UNAVAILABLE"
    assert row["sample"]["source_metadata"]["raw_sdk_json"]
    assert bool(item.errors) is (ending == "active_failure")
    item.pending.mkdir(parents=True)
    item._summary()
    report = json.loads((item.pending / "summary.json").read_text())
    assert report["status"] == ("FAILED" if ending == "active_failure" else "COLLECTED")


def test_fullscreen_transition_requires_fresh_geometry(tmp_path):
    calls = []
    class Counting(Probe):
        def snapshot(self, hwnd, size):
            calls.append((hwnd, size))
            return super().snapshot(hwnd, size)
    item = collector(tmp_path, probe_factory=Counting)
    ready(item)
    item._sample(sample())
    item.suspend_presentation()
    item._sample(sample())
    item.presentation = (time.monotonic_ns(), 2, (1920, 1080))
    item.last_present_ns = time.monotonic_ns()
    item._sample(sample())
    assert calls == [(1, (1280, 720)), (2, (1920, 1080))]
    rows = [json.loads(line) for line in item.gaze.getvalue().splitlines()]
    assert [row["aoi"] for row in rows if row["record_type"] == "gaze"] == ["FORWARD", "NO_AOI", "FORWARD"]


def test_end_presentation_then_worker_drain_saves_collected_summary(tmp_path):
    class LateSource(Source):
        def subscribe(self, callback):
            self.callback = callback
            super().subscribe(callback)
        def unsubscribe(self):
            self.callback(sample())
    item = collector(tmp_path, source_factory=LateSource, probe_factory=Probe)
    item.presentation = (time.monotonic_ns(), 1, (1280, 720))
    item.last_present_ns = time.monotonic_ns()
    item.start()
    deadline = time.monotonic() + 3
    while not item.counts["samples"] and time.monotonic() < deadline:
        time.sleep(.01)
    item.end_presentation()
    item.close()
    report = json.loads(next((item.directory / "gaze").glob("*/summary.json")).read_text())
    assert report["status"] == "COLLECTED" and report["errors"] == []
    assert report["counts"]["mapping_reason:PRESENTATION_ENDED"] == 1
