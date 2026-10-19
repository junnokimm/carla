"""Preload the private SDL before pygame; never initialize SDL or touch a device."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import struct
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[2] / "native/tsxw_sdl"
HINT = b"P8_TSXW_PRESERVE_FORCES"
REVISION = b"SDL-release-2.28.4-0-gcc016b004 (P8 TS-XW preserve existing forces v1)"
_library = None  # Retain the module for the entire process, including pygame.quit().


def _load_private_sdl(path: Path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    ]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    loaded = kernel.GetModuleHandleW("SDL2.dll")
    if loaded:
        name = ctypes.create_unicode_buffer(32768)
        if not kernel.GetModuleFileNameW(loaded, name, len(name)):
            raise OSError("Cannot identify the already loaded SDL2.dll")
        if Path(name.value).resolve() != path.resolve():
            raise OSError(
                "pygame/SDL was loaded before the resistance-preserving launcher"
            )
    # Absolute path, normal Windows dependency search, no PATH or package rewrites.
    return ctypes.CDLL(str(path), winmode=0x00001100)


def preserve_existing_resistance() -> dict:
    """Enable only VID 044f/PID b692's input-only open policy.

    Missing/incompatible local assets leave the original startup available with
    an explicit warning. No recovery, polling or control code is replaced.
    """
    global _library
    if os.name != "nt":
        return {"status": "TSXW_RESISTANCE_UNAVAILABLE", "reason": "Windows only"}
    try:
        if struct.calcsize("P") != 8:
            raise ValueError("The private SDL requires the existing x64 pilot Python")
        path = DIRECTORY / "SDL2.dll"
        manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["dll_sha256"]:
            raise ValueError("Private SDL checksum mismatch")
        library = _load_private_sdl(path)
        library.SDL_GetRevision.argtypes = []
        library.SDL_GetRevision.restype = ctypes.c_char_p
        if library.SDL_GetRevision() != REVISION:
            raise ValueError("Unexpected private SDL revision")
        # SDL_Quit clears SDL hints at module boundaries. SDL's environment entry
        # survives, so subsequent device opens use the same preservation policy.
        library.SDL_setenv.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        library.SDL_setenv.restype = ctypes.c_int
        library.SDL_SetHintWithPriority.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        library.SDL_SetHintWithPriority.restype = ctypes.c_int
        if library.SDL_setenv(HINT, b"1", 1) != 0:
            raise OSError("Could not retain the preservation policy across SDL_Quit")
        if not library.SDL_SetHintWithPriority(HINT, b"1", 2):
            raise OSError("Could not enable the TS-XW preservation policy")
        _library = library
        return {
            "status": "TSXW_RESISTANCE_PRESERVE_ENABLED",
            "device": "044f:b692",
            "sdl_version": "2.28.4",
            "physical_verification": "pending",
        }
    except (OSError, ValueError, KeyError, AttributeError) as error:
        return {
            "status": "TSXW_RESISTANCE_UNAVAILABLE",
            "reason": str(error),
            "warning": "Resistance preservation is unavailable; existing pilot startup continues.",
        }
