from __future__ import annotations

import json
from types import SimpleNamespace

import carla
import pytest

from src.scenario.camera_feed import CameraFeedSnapshot
from src.scenario.driver_view import CameraFeed, DriverViewConfig
from src.scenario.research_camera_diagnostics import ResearchCameraDiagnostics
from src.scenario.research_camera_metadata import ResearchCameraRunMeasurements
from tests.test_driver_view import FakeSensor


class FakeImage:
    def __init__(self, frame: int, timestamp: float) -> None:
        self.frame = frame
        self.timestamp = timestamp


class StepClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def test_summary_counts_same_frame_redelivery_latest_reuse_and_no_image() -> None:
    clock = StepClock(10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6)
    diagnostics = ResearchCameraDiagnostics(
        clock=clock,
        run_id="aba-a1",
        composition="front_left_right_3_rgb",
    )
    front = CameraFeed("front", FakeSensor())
    left = CameraFeed("left", FakeSensor())
    front.receipt_clock = clock
    left.receipt_clock = clock
    front.receive(FakeImage(100, 5.0))

    diagnostics.record_camera_preparation("front", front.snapshot(), clock())
    front.receive(FakeImage(100, 5.0))
    diagnostics.record_camera_preparation("front", front.snapshot(), clock())
    diagnostics.record_camera_preparation("front", front.snapshot(), clock())
    diagnostics.record_camera_preparation("left", left.snapshot(), clock())
    payload = json.loads(diagnostics.to_json((front, left)))

    assert payload["sensor_count"] == 2
    assert payload["roles"]["front"]["callback_count"] == 2
    assert payload["roles"]["front"]["prepared_count"] == 3
    assert payload["roles"]["front"]["updated_count"] == 1
    assert payload["roles"]["front"]["repeated_count"] == 2
    assert payload["roles"]["front"]["no_image_count"] == 0
    assert payload["roles"]["front"]["latest_preparation_host_age_seconds"] == 0.2
    assert (
        payload["roles"]["front"]["latest_receipt_host_age_at_summary_seconds"] == 0.4
    )
    assert payload["roles"]["left"]["no_image_count"] == 1
    assert payload["measurement_window"] == {
        "start_host_monotonic_seconds": 10.1,
        "end_host_monotonic_seconds": 10.5,
        "definition": "first through last render preparation",
    }
    assert payload["measurement_lifecycle"]["callback_counts"] == {
        "start": "sensor attachment and listen registration",
        "end": "summary snapshot",
        "definition": "every camera callback received during that lifecycle",
    }


def test_summary_preserves_first_last_callback_metadata_and_role_isolation() -> None:
    clock = StepClock(20.0, 21.0, 22.0)
    diagnostics = ResearchCameraDiagnostics(
        clock=clock,
        run_id="aba-b",
        composition="front_only_1_rgb",
    )
    front = CameraFeed("front", FakeSensor())
    front.receipt_clock = clock
    front.receive(FakeImage(200, 8.0))
    front.receive(FakeImage(201, 8.05))

    payload = json.loads(diagnostics.to_json((front,)))
    role = payload["roles"]["front"]

    assert payload["run"]["run_id"] == "aba-b"
    assert payload["run"]["composition"] == "front_only_1_rgb"
    assert set(payload["roles"]) == {"front"}
    assert role["callback_count"] == 2
    assert role["first_sensor_frame"] == 200
    assert role["last_sensor_frame"] == 201
    assert role["first_sensor_timestamp_seconds"] == 8.0
    assert role["last_sensor_timestamp_seconds"] == 8.05
    assert role["first_host_receipt_seconds"] == 20.0
    assert role["last_host_receipt_seconds"] == 21.0
    assert payload["summary_host_monotonic_seconds"] == 22.0
    assert role["latest_receipt_host_age_at_summary_seconds"] == 1.0


def test_generation_is_fallback_identity_when_sensor_metadata_is_missing() -> None:
    diagnostics = ResearchCameraDiagnostics(
        clock=lambda: 3.0,
        composition="front_only_1_rgb",
    )
    sensor = FakeSensor()
    image = FakeImage(1, 1.0)
    first = CameraFeedSnapshot(
        image=image,
        generation=1,
        callback_count=1,
        first_sensor_frame=None,
        last_sensor_frame=None,
        first_sensor_timestamp_seconds=None,
        last_sensor_timestamp_seconds=None,
        first_host_receipt_seconds=1.0,
        last_host_receipt_seconds=1.0,
    )
    second = CameraFeedSnapshot(
        image=image,
        generation=2,
        callback_count=2,
        first_sensor_frame=None,
        last_sensor_frame=None,
        first_sensor_timestamp_seconds=None,
        last_sensor_timestamp_seconds=None,
        first_host_receipt_seconds=1.0,
        last_host_receipt_seconds=2.0,
    )
    feed = CameraFeed("front", sensor)

    diagnostics.record_camera_preparation("front", first, 1.5)
    diagnostics.record_camera_preparation("front", second, 2.5)
    payload = json.loads(diagnostics.to_json((feed,)))

    assert payload["roles"]["front"]["updated_count"] == 2
    assert payload["roles"]["front"]["repeated_count"] == 0
    assert payload["roles"]["front"]["sensor"]["id"] == "unknown"
    assert payload["roles"]["front"]["sensor"]["type_id"] == "unknown"
    assert (
        payload["roles"]["front"]["sensor"]["world_transform_at_summary"] == "unknown"
    )
    assert (
        payload["roles"]["front"]["sensor"]["transform_coordinate_provenance"]
        == "unknown"
    )


def test_summary_reports_runtime_sensor_attributes_or_explicit_unknown() -> None:
    diagnostics = ResearchCameraDiagnostics(
        clock=lambda: 1.0,
        run_id=None,
        composition="front_only_1_rgb",
    )
    sensor = FakeSensor()
    sensor.attributes = {
        "image_size_x": "1280",
        "image_size_y": "720",
        "fov": "100.0",
    }
    sensor.id = 314
    sensor.type_id = "sensor.camera.rgb"
    sensor.get_transform = lambda: carla.Transform(
        carla.Location(x=1.4, y=0.0, z=1.3),
        carla.Rotation(pitch=-2.0),
    )
    front = CameraFeed("front", sensor)

    payload = json.loads(diagnostics.to_json((front,), DriverViewConfig()))
    metadata = payload["roles"]["front"]["sensor"]

    assert metadata["image_size_x"] == "1280"
    assert metadata["image_size_y"] == "720"
    assert metadata["fov"] == "100.0"
    assert metadata["sensor_tick"] == "unknown"
    assert metadata["id"] == 314
    assert metadata["type_id"] == "sensor.camera.rgb"
    assert metadata["transform_coordinate_provenance"] == {
        "coordinate_frame": "world",
        "captured_at": "summary",
        "source": "sensor.get_transform",
    }
    assert metadata["requested_configuration"] == {
        "image_size_x": 1280,
        "image_size_y": 720,
        "fov": 100.0,
        "vehicle_relative_mount": {
            "x": 1.4,
            "y": 0.0,
            "z": 1.3,
            "pitch": -2.0,
            "yaw": 0.0,
            "roll": 0.0,
        },
        "coordinate_frame": "vehicle_relative",
        "source": "ResearchDriverViewConfig",
    }
    assert metadata["world_transform_at_summary"] == pytest.approx(
        {
            "x": 1.4,
            "y": 0.0,
            "z": 1.3,
            "pitch": -2.0,
            "yaw": 0.0,
            "roll": 0.0,
        }
    )
    assert metadata["transform"] == pytest.approx(
        {
            "x": 1.4,
            "y": 0.0,
            "z": 1.3,
            "pitch": -2.0,
            "yaw": 0.0,
            "roll": 0.0,
        }
    )


def test_summary_reports_requested_rear_camera_configuration() -> None:
    diagnostics = ResearchCameraDiagnostics(
        clock=lambda: 1.0,
        composition="front_rear_left_right_4_rgb",
    )
    rear = CameraFeed("rear", FakeSensor())

    payload = json.loads(diagnostics.to_json((rear,), DriverViewConfig()))
    requested = payload["roles"]["rear"]["sensor"]["requested_configuration"]

    assert requested == {
        "image_size_x": 640,
        "image_size_y": 180,
        "fov": 90.0,
        "vehicle_relative_mount": {
            "x": -2.5,
            "y": 0.0,
            "z": 1.3,
            "pitch": 0.0,
            "yaw": 180.0,
            "roll": 0.0,
        },
        "coordinate_frame": "vehicle_relative",
        "source": "ResearchDriverViewConfig",
    }


def test_summary_captures_read_only_world_weather_and_settings() -> None:
    class FakeWorld:
        def get_map(self):
            return SimpleNamespace(name="Town04")

        def get_weather(self):
            return SimpleNamespace(cloudiness=25.0, precipitation=0.0)

        def get_settings(self):
            return SimpleNamespace(
                synchronous_mode=False,
                fixed_delta_seconds=None,
                no_rendering_mode=False,
            )

    diagnostics = ResearchCameraDiagnostics(
        clock=lambda: 1.0,
        run_id="environment",
        composition="front_only_1_rgb",
    )

    diagnostics.capture_environment(FakeWorld())
    environment = json.loads(diagnostics.to_json(()))["environment"]

    assert environment["map_name"] == "Town04"
    assert environment["weather"]["cloudiness"] == 25.0
    assert environment["weather"]["fog_density"] == "unknown"
    assert environment["world_settings"]["synchronous_mode"] is False
    assert environment["world_settings"]["max_substeps"] == "unknown"


def test_duration_summary_uses_scalar_count_mean_and_max() -> None:
    diagnostics = ResearchCameraDiagnostics(
        clock=lambda: 1.0,
        run_id="segments",
        composition="front_only_1_rgb",
    )

    diagnostics.record_duration("display_flip", 0.002)
    diagnostics.record_duration("display_flip", 0.004)
    payload = json.loads(diagnostics.to_json(()))

    assert payload["timing"]["display_flip"] == {
        "count": 2,
        "mean_ms": 3.0,
        "max_ms": 4.0,
        "total_ms": 6.0,
    }
    assert payload["timing_definitions"]["rounded_ms_decimal_places"] == 6
    assert payload["limitations"]["rounded_zero_duration_is_zero_cost"] is False


def test_summary_reports_default_clock_metadata_and_run_measurements() -> None:
    from time import get_clock_info

    diagnostics = ResearchCameraDiagnostics(composition="front_only_1_rgb")
    measurements = ResearchCameraRunMeasurements(
        initial_world_frame=120,
        final_world_frame=180,
        initial_simulation_seconds=40.0,
        final_simulation_seconds=60.0,
        host_elapsed_seconds=20.1,
        loop_count=57,
    )

    payload = json.loads(diagnostics.to_json((), run_measurements=measurements))
    clock_info = get_clock_info("perf_counter")

    assert payload["clocks"]["host"] == "time.perf_counter seconds"
    assert payload["clocks"]["host_implementation"] == clock_info.implementation
    assert payload["clocks"]["host_resolution_seconds"] == pytest.approx(
        clock_info.resolution
    )
    assert payload["clocks"]["run_measurements_host_elapsed"] == (
        "separate duration from the session monotonic clock"
    )
    assert payload["run_measurements"] == {
        "initial_world_frame": 120,
        "final_world_frame": 180,
        "world_frame_delta": 60,
        "initial_simulation_seconds": 40.0,
        "final_simulation_seconds": 60.0,
        "simulation_elapsed_seconds": 20.0,
        "host_elapsed_seconds": 20.1,
        "loop_count": 57,
    }


def test_summary_marks_injected_clock_metadata_unknown() -> None:
    diagnostics = ResearchCameraDiagnostics(
        clock=lambda: 1.0,
        composition="front_only_1_rgb",
    )

    payload = json.loads(diagnostics.to_json(()))

    assert payload["clocks"]["host_implementation"] == "unknown"
    assert payload["clocks"]["host_resolution_seconds"] == "unknown"
