from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.p6.aoi import (
    Aoi,
    AoiLayout,
    ContinuousAoiTimer,
    DisplayMapping,
    Point,
    Polygon,
    Rect,
)
from src.p6.clock import AffineClockMapping, ClockPair, SessionOrigin
from src.p6.models import ClockKind, EyeDatum, RawGazeSample, Validity
from src.p6.source import (
    GazeCallback,
    GazeSource,
    RealSourceUnavailableError,
    SyntheticGazeSource,
    preflight_source,
    unavailable_real_source,
)
from src.p6.writer import GazeSidecarWriter


def test_affine_mapping_requires_five_ordered_pairs_and_preserves_origin() -> None:
    origin = SessionOrigin(monotonic_ns=1_000_000_000, utc_ns=2_000_000_000)
    pairs = tuple(
        ClockPair(
            source_value=index * 1_000_000,
            host_monotonic_ns=1_000_000_000 + index * 1_000_000_000,
        )
        for index in range(5)
    )

    mapping = AffineClockMapping.fit("us", pairs, origin)

    assert mapping.fitted is True
    assert mapping.verified is False
    assert mapping.estimate_session_seconds(2_000_000) == pytest.approx(2.0)
    assert mapping.to_session_seconds(2_000_000) is None
    assert mapping.provenance.pair_count == 5


@pytest.mark.parametrize(
    "pairs",
    [
        tuple(ClockPair(index, index) for index in range(4)),
        (
            ClockPair(0, 0),
            ClockPair(1, 1),
            ClockPair(1, 2),
            ClockPair(3, 3),
            ClockPair(4, 4),
        ),
        (
            ClockPair(0, 0),
            ClockPair(1, 2),
            ClockPair(2, 1),
            ClockPair(3, 3),
            ClockPair(4, 4),
        ),
    ],
)
def test_affine_mapping_rejects_unverified_pair_sets(
    pairs: tuple[ClockPair, ...],
) -> None:
    mapping = AffineClockMapping.fit("ns", pairs, SessionOrigin(0, 0))

    assert mapping.verified is False
    assert mapping.to_session_seconds(10) is None


def test_aoi_transform_rejects_deliberately_clipped_rear_fixture() -> None:
    mapping = DisplayMapping(
        desktop=Rect(0, 0, 1920, 1080),
        display=Rect(0, 0, 1280, 720),
        client=Rect(0, 0, 1280, 720),
        display_mapping_verified=True,
        client_mapping_verified=True,
    )
    layout = AoiLayout(
        version="aoi-v1",
        mapping=mapping,
        forward=Polygon((Point(0, 0), Point(1280, 0), Point(1280, 720), Point(0, 720))),
        rear=Rect(980, 72, 320, 120),
        left=Rect(20, 500, 320, 180),
        right=Rect(940, 500, 320, 180),
        tablet=None,
        other=(),
        tablet_mapping_verified=False,
    )

    report = layout.validate()

    assert report.valid is False
    assert "rear:clipped" in report.issues
    assert layout.classify(0.5, 0.5) is Aoi.INVALID_LAYOUT
    assert report.tablet_status == "NOT_TESTED"


def _sample(sequence: int, source_timestamp: int) -> RawGazeSample:
    eye = EyeDatum(0.5, 0.5, Validity.VALID, None)
    return RawGazeSample(
        source_sequence=sequence,
        source_timestamp=source_timestamp,
        source_unit="us",
        clock_kind=ClockKind.DEVICE,
        left=eye,
        right=eye,
        source_metadata={"provider": "synthetic", "synthetic": True},
    )


def test_writer_marks_synthetic_records_and_sequence_anomalies(tmp_path: Path) -> None:
    writer = GazeSidecarWriter(
        path=tmp_path / "gaze.jsonl",
        run_id="run-p6",
        origin=SessionOrigin(1_000, 2_000),
        mapping=AffineClockMapping.unverified("us", SessionOrigin(1_000, 2_000)),
        layout=None,
        queue_capacity=8,
    )
    writer.start()
    writer.capture(_sample(1, 10), receive_monotonic_ns=2_000, receive_utc_ns=3_000)
    writer.capture(_sample(1, 9), receive_monotonic_ns=3_000, receive_utc_ns=4_000)
    writer.close()

    rows = [
        json.loads(line) for line in (tmp_path / "gaze.jsonl").read_text().splitlines()
    ]
    samples = [row for row in rows if row["record_type"] == "gaze"]
    assert all(row["synthetic"] is True for row in samples)
    assert samples[1]["duplicate_sequence"] is True
    assert samples[1]["backward_source_time"] is True
    assert samples[0]["acquisition_session_seconds"] is None


def test_real_source_is_explicitly_blocked_without_confirmed_adapter() -> None:
    with pytest.raises(RealSourceUnavailableError) as caught:
        unavailable_real_source()

    assert caught.value.status == "BLOCKED_MODEL_SDK_UNCONFIRMED"


def test_synthetic_source_calibration_is_not_aoi_validation() -> None:
    source = SyntheticGazeSource(sample_hz=60.0)

    calibration = source.calibrate()

    assert calibration.status == "SDK_SUCCESS_SYNTHETIC"
    assert calibration.aoi_validation_passed is False


@pytest.mark.parametrize("status", ("DISCONNECTED", "SDK_ABSENT", "CALIBRATION_FAILED"))
def test_source_preflight_exposes_failure_statuses(status: str) -> None:
    from src.p6.models import CalibrationResult, DiscoveredDevice, SourceStatus

    class FailedSource:
        @property
        def status(self) -> SourceStatus:
            return SourceStatus(status)

        def discover(self) -> tuple[DiscoveredDevice, ...]:
            return ()

        def select(self, identifier: str) -> None:
            return

        def calibrate(self) -> CalibrationResult:
            return CalibrationResult("CALIBRATION_FAILED", False)

        def subscribe(self, callback: GazeCallback) -> None:
            return

        def unsubscribe(self) -> None:
            return

    source: GazeSource = FailedSource()

    with pytest.raises(RealSourceUnavailableError) as caught:
        preflight_source(source)

    assert caught.value.status == status


def test_continuous_aoi_timer_uses_elapsed_time_emits_once_and_resets_on_invalid() -> (
    None
):
    timer = ContinuousAoiTimer(gap_policy_seconds=0.100)

    assert timer.observe(Aoi.REAR_MIRROR, 0.0, True) is False
    assert timer.observe(Aoi.REAR_MIRROR, 0.1, True) is False
    assert timer.observe(Aoi.REAR_MIRROR, 0.16, True) is True
    assert timer.observe(Aoi.REAR_MIRROR, 0.20, True) is False
    assert timer.observe(Aoi.NO_AOI, None, False) is False
    assert timer.observe(Aoi.REAR_MIRROR, 0.30, True) is False


def test_writer_reports_bounded_queue_overflow_and_exclusive_run_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exclusive.jsonl"
    origin = SessionOrigin(0, 0)
    writer = GazeSidecarWriter(
        path=path,
        run_id="exclusive",
        origin=origin,
        mapping=AffineClockMapping.unverified("us", origin),
        layout=None,
        queue_capacity=1,
    )
    with pytest.raises(FileExistsError):
        GazeSidecarWriter(
            path=path,
            run_id="exclusive",
            origin=origin,
            mapping=AffineClockMapping.unverified("us", origin),
            layout=None,
        )
    writer.capture(_sample(10, 10), receive_monotonic_ns=10, receive_utc_ns=10)
    writer.capture(_sample(11, 11), receive_monotonic_ns=11, receive_utc_ns=11)
    writer.start()
    writer.close()

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    overflow = next(row for row in rows if row["record_type"] == "overflow")
    assert overflow["count"] == 1
    assert overflow["first_sequence"] == 11
    assert overflow["last_sequence"] == 11


def test_writer_error_is_observable(tmp_path: Path) -> None:
    writer = GazeSidecarWriter(
        path=tmp_path / "failure.jsonl",
        run_id="failure",
        origin=SessionOrigin(0, 0),
        mapping=AffineClockMapping.unverified("us", SessionOrigin(0, 0)),
        layout=None,
    )

    def fail_write(record) -> None:
        raise ValueError("injected writer failure")

    writer._write = fail_write
    writer.start()
    writer.capture(_sample(1, 1), receive_monotonic_ns=1, receive_utc_ns=1)
    writer.close()

    assert isinstance(writer.worker_error, ValueError)
