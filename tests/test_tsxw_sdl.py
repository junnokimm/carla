import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.p8 import tsxw_sdl
from tools import run_pilot_preserve_forces


class FakeFunction:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


@pytest.fixture
def private_sdl(tmp_path, monkeypatch):
    monkeypatch.setattr(tsxw_sdl, "DIRECTORY", tmp_path)
    (tmp_path / "SDL2.dll").write_bytes(b"fake DLL, never loaded")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "dll_sha256": hashlib.sha256(b"fake DLL, never loaded").hexdigest(),
            }
        )
    )
    library = SimpleNamespace(
        SDL_GetRevision=FakeFunction(tsxw_sdl.REVISION),
        SDL_setenv=FakeFunction(0),
        SDL_SetHintWithPriority=FakeFunction(1),
    )
    monkeypatch.setattr(tsxw_sdl, "_load_private_sdl", lambda path: library)
    monkeypatch.setattr(tsxw_sdl, "_library", None)
    return library


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_preload_retains_policy_across_module_boundaries(private_sdl):
    result = tsxw_sdl.preserve_existing_resistance()
    assert result["status"] == "TSXW_RESISTANCE_PRESERVE_ENABLED"
    assert private_sdl.SDL_setenv.calls == [(tsxw_sdl.HINT, b"1", 1)]
    assert private_sdl.SDL_SetHintWithPriority.calls == [(tsxw_sdl.HINT, b"1", 2)]
    assert tsxw_sdl._library is private_sdl


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
@pytest.mark.parametrize(
    "failure", ["missing", "checksum", "load", "revision", "environment", "hint"]
)
def test_unavailable_preservation_reports_warning_without_aborting(
    private_sdl,
    monkeypatch,
    failure,
):
    if failure == "missing":
        (tsxw_sdl.DIRECTORY / "manifest.json").unlink()
    elif failure == "checksum":
        (tsxw_sdl.DIRECTORY / "SDL2.dll").write_bytes(b"different")
    elif failure == "load":

        def fail(_):
            raise OSError("already loaded")

        monkeypatch.setattr(tsxw_sdl, "_load_private_sdl", fail)
    elif failure == "revision":
        private_sdl.SDL_GetRevision.result = b"unexpected"
    elif failure == "environment":
        private_sdl.SDL_setenv.result = -1
    elif failure == "hint":
        private_sdl.SDL_SetHintWithPriority.result = 0
    result = tsxw_sdl.preserve_existing_resistance()
    assert result["status"] == "TSXW_RESISTANCE_UNAVAILABLE"
    assert result["reason"]
    assert "continues" in result["warning"]


@pytest.mark.parametrize("code", [0, 2, 130])
@pytest.mark.parametrize("argv", [None, ["--m2", "1"], ["--m2", "2"]])
def test_wrapper_forwards_args_and_exit_code_after_policy(monkeypatch, argv, code):
    order = []
    monkeypatch.setattr(
        run_pilot_preserve_forces,
        "preserve_existing_resistance",
        lambda: order.append("prepare") or {"status": "unavailable"},
    )

    def pilot(received):
        assert order == ["prepare"]
        assert received is argv
        return code

    monkeypatch.setitem(sys.modules, "tools.run_pilot", SimpleNamespace(main=pilot))
    assert run_pilot_preserve_forces.main(argv) == code


@pytest.mark.skipif(os.name != "nt", reason="Project-private Windows SDL")
def test_real_sdl_binding_reinitialization_and_dummy_av_without_devices():
    script = r"""
import ctypes
import json
from src.p8.tsxw_sdl import preserve_existing_resistance
from src.p8 import tsxw_sdl
assert preserve_existing_resistance()["status"] == "TSXW_RESISTANCE_PRESERVE_ENABLED"
import pygame
sdl = tsxw_sdl._library
sdl.SDL_WasInit.argtypes = [ctypes.c_uint32]
sdl.SDL_WasInit.restype = ctypes.c_uint32
sdl.SDL_GetHintBoolean.argtypes = [ctypes.c_char_p, ctypes.c_int]
sdl.SDL_GetHintBoolean.restype = ctypes.c_int
assert sdl.SDL_WasInit(0) == 0
assert pygame.get_sdl_version() == (2, 28, 4)
# Decode the recorded pilot's GUID with the real SDL parser (no enumeration).
class Guid(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]
sdl.SDL_JoystickGetGUIDFromString.argtypes = [ctypes.c_char_p]
sdl.SDL_JoystickGetGUIDFromString.restype = Guid
sdl.SDL_GetJoystickGUIDInfo.argtypes = [Guid] + [ctypes.POINTER(ctypes.c_uint16)] * 4
guid = sdl.SDL_JoystickGetGUIDFromString(b"0300fe4a4f04000092b6000000000000")
vendor, product = ctypes.c_uint16(), ctypes.c_uint16()
sdl.SDL_GetJoystickGUIDInfo(guid, ctypes.byref(vendor), ctypes.byref(product), None, None)
assert (vendor.value, product.value) == (0x044f, 0xb692)
for _ in range(4):
    assert sdl.SDL_GetHintBoolean(tsxw_sdl.HINT, 0) == 1
    # Never pygame.init(): that also initializes physical joystick enumeration.
    pygame.display.init()
    assert sdl.SDL_WasInit(0x20) == 0x20  # pygame bound to this exact SDL module.
    assert sdl.SDL_WasInit(0x200) == 0  # joystick remains uninitialized.
    window = pygame.display.set_mode((32, 32))
    window.fill((12, 34, 56))
    assert tuple(window.get_at((1, 1)))[:3] == (12, 34, 56)
    pygame.font.init()
    assert pygame.font.Font(None, 16).render("P8", True, "white").get_width() > 0
    pygame.event.post(pygame.event.Event(pygame.USEREVENT, marker="p8"))
    assert any(getattr(event, "marker", None) == "p8" for event in pygame.event.get())
    pygame.mixer.init()
    sound = pygame.mixer.Sound(buffer=b"\0" * 4096)
    assert sound.play() is not None
    pygame.display.flip()
    pygame.quit()
    assert sdl.SDL_WasInit(0) == 0
    assert sdl.SDL_GetHintBoolean(tsxw_sdl.HINT, 0) == 1
print(json.dumps({"binding": "private", "reinitializations": 4, "hardware_opened": False}))
"""
    environment = os.environ | {
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        "PYGAME_HIDE_SUPPORT_PROMPT": "1",
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["reinitializations"] == 4
