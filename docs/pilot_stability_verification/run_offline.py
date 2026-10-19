"""Run regression tests without connecting to CARLA or opening physical input."""

import os
import sys
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "--preserve-tsxw-sdl" in sys.argv:
    sys.argv.remove("--preserve-tsxw-sdl")
    from src.p8.tsxw_sdl import preserve_existing_resistance

    result = preserve_existing_resistance()
    print(result, flush=True)
    if result["status"] != "TSXW_RESISTANCE_PRESERVE_ENABLED":
        raise RuntimeError("Verification must use the private SDL")

if "--project-noa-tm" in sys.argv:
    sys.argv.remove("--project-noa-tm")
    from src.p8.noa_tm import enable_lane_change_runtime

    print(enable_lane_change_runtime(), flush=True)

import carla
import pygame
import pytest


def forbidden(*args, **kwargs):
    raise AssertionError("Offline verification forbids CARLA Client/physical joystick")


carla.Client = forbidden
pygame.joystick.Joystick = forbidden
raise SystemExit(pytest.main(["-p", "no:cacheprovider", *sys.argv[1:]]))
