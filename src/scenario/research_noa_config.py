from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Final

from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig

DEFAULT_RESEARCH_VEHICLE_BLUEPRINT: Final = "vehicle.mercedes.coupe_2020"
RESEARCH_LIVE_SMOKE_MAX_DURATION_SECONDS: Final = 20.0


class ResearchNoARunMode(Enum):
    DRY_RUN = "dry-run"
    LIVE_SMOKE = "live-smoke"


@dataclass(frozen=True, slots=True)
class ResearchNoAConfigError(ValueError):
    field: str
    requirement: str

    def __str__(self) -> str:
        return f"{self.field} {self.requirement}"


@dataclass(frozen=True, slots=True)
class ResearchNoARunConfig:
    duration: float
    control_config: NoAControlConfig
    mode: ResearchNoARunMode = ResearchNoARunMode.DRY_RUN
    spawn_index: int | None = None
    host: str = "127.0.0.1"
    port: int = 2000
    timeout: float = 5.0
    vehicle_blueprint: str = DEFAULT_RESEARCH_VEHICLE_BLUEPRINT
    front_camera_only: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.duration) or self.duration <= 0.0:
            raise ResearchNoAConfigError("duration", "must be finite and > 0")
        if self.spawn_index is not None and self.spawn_index < 0:
            raise ResearchNoAConfigError("spawn_index", "must be >= 0")
        if self.mode is ResearchNoARunMode.LIVE_SMOKE:
            if self.spawn_index is None:
                raise ResearchNoAConfigError(
                    "spawn_index",
                    "is required for live smoke",
                )
            self._validate_live_safety_caps()

    def _validate_live_safety_caps(self) -> None:
        longitudinal = self.control_config.longitudinal
        lateral = self.control_config.lateral
        capped_values = (
            (
                "duration",
                self.duration,
                RESEARCH_LIVE_SMOKE_MAX_DURATION_SECONDS,
            ),
            ("target_speed_kmh", longitudinal.target_speed_kmh, 20.0),
            ("max_throttle", longitudinal.max_throttle, 0.25),
            ("max_brake", longitudinal.max_brake, 0.5),
            ("max_steering", lateral.max_steering, 0.15),
        )
        for name, value, cap in capped_values:
            if value > cap:
                raise ResearchNoAConfigError(name, f"must be <= {cap}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated research NoA validation surface."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        dest="mode",
        action="store_const",
        const=ResearchNoARunMode.DRY_RUN,
        help="Keep manual input disabled and never activate custom control.",
    )
    mode.add_argument(
        "--live-smoke",
        dest="mode",
        action="store_const",
        const=ResearchNoARunMode.LIVE_SMOKE,
        help="Run the bounded custom-control smoke after spawn preflight.",
    )
    parser.add_argument("--spawn-index", type=int)
    parser.add_argument("--duration", required=False, type=float, default=5.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--vehicle-blueprint",
        default=DEFAULT_RESEARCH_VEHICLE_BLUEPRINT,
    )
    parser.add_argument("--front-camera-only", action="store_true")
    parser.add_argument("--target-speed-kmh", required=True, type=float)
    parser.add_argument("--speed-deadband-kmh", required=True, type=float)
    parser.add_argument("--acceleration-gain", required=True, type=float)
    parser.add_argument("--braking-gain", required=True, type=float)
    parser.add_argument("--max-throttle", required=True, type=float)
    parser.add_argument("--max-brake", required=True, type=float)
    parser.add_argument("--lateral-error-gain", required=True, type=float)
    parser.add_argument("--heading-error-gain", required=True, type=float)
    parser.add_argument("--lateral-deadband-m", required=True, type=float)
    parser.add_argument("--heading-deadband-rad", required=True, type=float)
    parser.add_argument("--max-steering", required=True, type=float)
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> ResearchNoARunConfig:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.mode is ResearchNoARunMode.LIVE_SMOKE and args.spawn_index is None:
        parser.error("--spawn-index is required with --live-smoke")

    try:
        control_config = NoAControlConfig(
            longitudinal=LongitudinalControlConfig(
                target_speed_kmh=args.target_speed_kmh,
                speed_deadband_kmh=args.speed_deadband_kmh,
                acceleration_gain=args.acceleration_gain,
                braking_gain=args.braking_gain,
                max_throttle=args.max_throttle,
                max_brake=args.max_brake,
            ),
            lateral=LateralControlConfig(
                lateral_error_gain=args.lateral_error_gain,
                heading_error_gain=args.heading_error_gain,
                lateral_deadband_m=args.lateral_deadband_m,
                heading_deadband_rad=args.heading_deadband_rad,
                max_steering=args.max_steering,
            ),
        )
        return ResearchNoARunConfig(
            duration=args.duration,
            control_config=control_config,
            mode=args.mode,
            spawn_index=args.spawn_index,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            vehicle_blueprint=args.vehicle_blueprint,
            front_camera_only=args.front_camera_only,
        )
    except ValueError as error:
        parser.error(str(error))
