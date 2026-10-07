from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import get_clock_info
from typing import Final

import carla

from src.scenario.driver_view import DriverViewConfig

UNKNOWN: Final = "unknown"
REPOSITORY_ROOT: Final = Path(__file__).parents[2]
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class ResearchCameraRunMeasurements:
    initial_world_frame: int
    final_world_frame: int
    initial_simulation_seconds: float
    final_simulation_seconds: float
    host_elapsed_seconds: float
    loop_count: int


def _clock_payload(uses_default_clock: bool) -> dict[str, JsonValue]:
    clock_info = get_clock_info("perf_counter") if uses_default_clock else None
    return {
        "host": (
            "time.perf_counter seconds"
            if uses_default_clock
            else "injected host clock seconds"
        ),
        "host_implementation": (
            clock_info.implementation if clock_info is not None else UNKNOWN
        ),
        "host_resolution_seconds": (
            clock_info.resolution if clock_info is not None else UNKNOWN
        ),
        "sensor": "CARLA image frame and timestamp seconds",
        "preparation_age": "render preparation host time minus latest callback host receipt",
        "summary_age": "summary host time minus latest callback host receipt",
        "run_measurements_host_elapsed": (
            "separate duration from the session monotonic clock"
        ),
        "cross_clock_subtraction": False,
    }


def _run_measurements_payload(
    measurements: ResearchCameraRunMeasurements | None,
) -> dict[str, JsonValue]:
    if measurements is None:
        return {
            name: UNKNOWN
            for name in (
                "initial_world_frame",
                "final_world_frame",
                "world_frame_delta",
                "initial_simulation_seconds",
                "final_simulation_seconds",
                "simulation_elapsed_seconds",
                "host_elapsed_seconds",
                "loop_count",
            )
        }
    return {
        "initial_world_frame": measurements.initial_world_frame,
        "final_world_frame": measurements.final_world_frame,
        "world_frame_delta": (
            measurements.final_world_frame - measurements.initial_world_frame
        ),
        "initial_simulation_seconds": measurements.initial_simulation_seconds,
        "final_simulation_seconds": measurements.final_simulation_seconds,
        "simulation_elapsed_seconds": (
            measurements.final_simulation_seconds
            - measurements.initial_simulation_seconds
        ),
        "host_elapsed_seconds": measurements.host_elapsed_seconds,
        "loop_count": measurements.loop_count,
    }


def _sensor_payload(
    sensor: carla.Actor,
    requested_configuration: JsonValue = UNKNOWN,
) -> dict[str, JsonValue]:
    attributes = getattr(sensor, "attributes", {})
    transform_method = getattr(sensor, "get_transform", None)
    transform = transform_method() if callable(transform_method) else None
    world_transform = (
        _transform_payload(transform) if transform is not None else UNKNOWN
    )
    return {
        "id": getattr(sensor, "id", UNKNOWN),
        "type_id": getattr(sensor, "type_id", UNKNOWN),
        "image_size_x": attributes.get("image_size_x", UNKNOWN),
        "image_size_y": attributes.get("image_size_y", UNKNOWN),
        "fov": attributes.get("fov", UNKNOWN),
        "sensor_tick": attributes.get("sensor_tick", UNKNOWN),
        "transform": world_transform,
        "world_transform_at_summary": world_transform,
        "transform_coordinate_provenance": {
            "coordinate_frame": "world",
            "captured_at": "summary",
            "source": "sensor.get_transform",
        }
        if transform is not None
        else UNKNOWN,
        "requested_configuration": requested_configuration,
    }


def _transform_payload(transform: carla.Transform) -> dict[str, float]:
    return {
        "x": float(transform.location.x),
        "y": float(transform.location.y),
        "z": float(transform.location.z),
        "pitch": float(transform.rotation.pitch),
        "yaw": float(transform.rotation.yaw),
        "roll": float(transform.rotation.roll),
    }


def _requested_sensor_payload(
    config: DriverViewConfig | None,
    role: str,
) -> JsonValue:
    if config is None:
        return UNKNOWN
    requests = {
        "front": (config.front_transform, config.front_resolution, config.fov),
        "rear": (
            config.rear_mirror.transform,
            config.rear_mirror.resolution,
            config.rear_mirror.fov,
        ),
        "left": (
            config.left_mirror_transform,
            config.mirror_resolution,
            config.mirror_fov,
        ),
        "right": (
            config.right_mirror_transform,
            config.mirror_resolution,
            config.mirror_fov,
        ),
    }
    request = requests.get(role)
    if request is None:
        return UNKNOWN
    mount, resolution, fov = request
    width, height = resolution
    return {
        "image_size_x": width,
        "image_size_y": height,
        "fov": fov,
        "vehicle_relative_mount": {
            "x": mount.x,
            "y": mount.y,
            "z": mount.z,
            "pitch": mount.pitch,
            "yaw": mount.yaw,
            "roll": mount.roll,
        },
        "coordinate_frame": "vehicle_relative",
        "source": "ResearchDriverViewConfig",
    }


def _environment_payload(world: carla.World) -> dict[str, JsonValue]:
    world_map = world.get_map()
    weather = world.get_weather()
    settings = world.get_settings()
    return {
        "map_name": str(world_map.name),
        "weather": {
            name: getattr(weather, name, UNKNOWN)
            for name in (
                "cloudiness",
                "precipitation",
                "precipitation_deposits",
                "wind_intensity",
                "sun_azimuth_angle",
                "sun_altitude_angle",
                "fog_density",
                "fog_distance",
                "wetness",
            )
        },
        "world_settings": {
            name: getattr(settings, name, UNKNOWN)
            for name in (
                "synchronous_mode",
                "fixed_delta_seconds",
                "no_rendering_mode",
                "substepping",
                "max_substep_delta_time",
                "max_substeps",
            )
        },
    }


def _git_state() -> tuple[str, bool | str]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return UNKNOWN, UNKNOWN
    if head.returncode != 0 or status.returncode != 0:
        return UNKNOWN, UNKNOWN
    return head.stdout.strip(), bool(status.stdout)
