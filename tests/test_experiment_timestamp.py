import math

import pytest

from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    ExternalClockTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
    TimestampValidationError,
)


def test_timestamp_envelope_preserves_distinct_clock_domains():
    host = HostClockTimestamp(monotonic_ns=125_000_000, utc_ns=1_790_000_000_000_000_000)
    carla = CarlaSnapshotTimestamp(
        simulation_seconds=12.5,
        frame=750,
        host_capture_started_monotonic_ns=124_900_000,
        host_capture_completed_monotonic_ns=124_950_000,
    )

    timestamp = TimestampEnvelope(host=host, carla_snapshot=carla)

    assert timestamp.host.monotonic_ns == 125_000_000
    assert timestamp.host.utc_ns == 1_790_000_000_000_000_000
    assert timestamp.carla_snapshot is not None
    assert timestamp.carla_snapshot.simulation_seconds == 12.5
    assert timestamp.carla_snapshot.frame == 750


def test_carla_snapshot_preserves_host_capture_window():
    timestamp = CarlaSnapshotTimestamp(
        simulation_seconds=1.25,
        frame=42,
        host_capture_started_monotonic_ns=10_000,
        host_capture_completed_monotonic_ns=10_100,
    )

    assert timestamp.host_capture_started_monotonic_ns == 10_000
    assert timestamp.host_capture_completed_monotonic_ns == 10_100


def test_timestamp_envelope_allows_missing_carla_and_external_timestamps():
    timestamp = TimestampEnvelope(
        host=HostClockTimestamp(monotonic_ns=1, utc_ns=2)
    )

    assert timestamp.carla_snapshot is None
    assert timestamp.external_clocks == ()


def test_timestamp_envelope_preserves_external_raw_timestamps():
    eye_tracker = ExternalClockTimestamp(
        source="eye_tracker",
        raw_value="000123456789",
        unit="device_tick",
    )
    tablet = ExternalClockTimestamp(
        source="tablet",
        raw_value="2026-09-28T10:15:30.123456Z",
    )

    timestamp = TimestampEnvelope(
        host=HostClockTimestamp(monotonic_ns=1, utc_ns=2),
        external_clocks=(eye_tracker, tablet),
    )

    assert timestamp.external_clocks == (eye_tracker, tablet)
    assert timestamp.external_clocks[0].raw_value == "000123456789"
    assert timestamp.external_clocks[1].unit is None


@pytest.mark.parametrize("field_name", ["monotonic_ns", "utc_ns"])
def test_host_clock_rejects_negative_values(field_name):
    values = {"monotonic_ns": 1, "utc_ns": 2}
    values[field_name] = -1

    with pytest.raises(TimestampValidationError, match=field_name):
        HostClockTimestamp(**values)


@pytest.mark.parametrize("simulation_seconds", [-0.1, math.inf, math.nan])
def test_carla_snapshot_rejects_invalid_simulation_seconds(simulation_seconds):
    with pytest.raises(TimestampValidationError, match="simulation_seconds"):
        CarlaSnapshotTimestamp(
            simulation_seconds=simulation_seconds,
            frame=1,
            host_capture_started_monotonic_ns=10,
            host_capture_completed_monotonic_ns=11,
        )


def test_carla_snapshot_rejects_negative_frame():
    with pytest.raises(TimestampValidationError, match="frame"):
        CarlaSnapshotTimestamp(
            simulation_seconds=0.0,
            frame=-1,
            host_capture_started_monotonic_ns=10,
            host_capture_completed_monotonic_ns=11,
        )


def test_carla_snapshot_rejects_reversed_host_capture_window():
    with pytest.raises(TimestampValidationError, match="capture"):
        CarlaSnapshotTimestamp(
            simulation_seconds=0.0,
            frame=0,
            host_capture_started_monotonic_ns=11,
            host_capture_completed_monotonic_ns=10,
        )


@pytest.mark.parametrize(
    ("source", "raw_value", "unit"),
    [
        ("", "123", None),
        ("eye_tracker", "", None),
        ("tablet", "123", ""),
    ],
)
def test_external_clock_rejects_blank_metadata(source, raw_value, unit):
    with pytest.raises(TimestampValidationError):
        ExternalClockTimestamp(source=source, raw_value=raw_value, unit=unit)
