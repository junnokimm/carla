"""Build the project-private SDL 2.28.4 with a TS-XW open-only adjustment.

Requires VS 2022 C++ and CMake. Does not load SDL or open any device.
Download the pinned official source archive separately, then pass --archive.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.libsdl.org/release/SDL2-2.28.4.zip"
SOURCE_SHA256 = "b53b9b42e731a33552d0a533316a88009b423c16a8a3a418df9ffe498c37da3d"
SOURCE_FILE = "src/joystick/windows/SDL_dinputjoystick.c"
VENDOR = "P8 TS-XW preserve existing forces v1"
REVISION = f"SDL-release-2.28.4-0-gcc016b004 ({VENDOR})"


def patch_source(original: str) -> str:
    """Change only the open function; all polling/mapping/cleanup stays upstream."""
    start = original.index("int SDL_DINPUT_JoystickOpen(")
    end = original.index("static int SDL_DINPUT_JoystickInitRumble(", start)
    body = original[start:end]
    replacements = (
        (
            "    DIPROPDWORD dipdw;\n",
            (
                "    DIPROPDWORD dipdw;\n"
                "    Uint16 vendor, product;\n"
                "    SDL_bool preserve_existing_forces;\n\n"
                "    /* P8 local change: leave TS-XW driver-managed resistance alone.\n"
                "     * Input format, axis ranges, ordering and polling are unchanged. */\n"
                "    SDL_GetJoystickGUIDInfo(joystickdevice->guid, &vendor, &product, NULL, NULL);\n"
                "    preserve_existing_forces = (vendor == 0x044f && product == 0xb692 &&\n"
                '        SDL_GetHintBoolean("P8_TSXW_PRESERVE_FORCES", SDL_FALSE));\n'
            ),
        ),
        (
            (
                "    /* Acquire shared access. Exclusive access is required for forces,\n"
                "     * though. */"
            ),
            "    /* P8 TS-XW input-only access must not claim exclusive force control. */",
        ),
        (
            "                                                DISCL_EXCLUSIVE |",
            "                                                (preserve_existing_forces ? DISCL_NONEXCLUSIVE : DISCL_EXCLUSIVE) |",
        ),
        (
            "    if (joystick->hwdata->Capabilities.dwFlags & DIDC_FORCEFEEDBACK) {",
            (
                "    if (!preserve_existing_forces &&\n"
                "        (joystick->hwdata->Capabilities.dwFlags & DIDC_FORCEFEEDBACK)) {"
            ),
        ),
    )
    for old, new in replacements:
        if body.count(old) != 1:
            raise ValueError(
                "Pinned SDL source does not match the expected open function"
            )
        body = body.replace(old, new, 1)
    return original[:start] + body + original[end:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Official SDL archive SHA256 mismatch")
    work = ROOT / "data/tsxw_sdl_build"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "SDL2-2.28.4"
    # Only verified archive members under its expected root may be extracted.
    with zipfile.ZipFile(args.archive) as archive:
        for member in archive.infolist():
            target = (work / member.filename).resolve()
            if not target.is_relative_to(source.resolve()):
                raise ValueError(f"Unexpected source archive entry: {member.filename}")
        archive.extractall(work)
    path = source / SOURCE_FILE
    original = path.read_text(encoding="utf-8")
    patched = patch_source(original)
    path.write_text(patched, encoding="utf-8", newline="\n")
    output = ROOT / "native/tsxw_sdl"
    output.mkdir(parents=True, exist_ok=True)
    patch = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{SOURCE_FILE}",
            tofile=f"b/{SOURCE_FILE}",
        )
    )
    (output / "preserve_forces.patch").write_text(patch, encoding="utf-8", newline="\n")
    cmake = shutil.which("cmake") or r"C:\Program Files\CMake\bin\cmake.exe"
    build = work / "build"
    subprocess.run(
        [
            cmake,
            "-S",
            str(source),
            "-B",
            str(build),
            "-G",
            "Visual Studio 17 2022",
            "-A",
            "x64",
            "-DSDL_SHARED=ON",
            "-DSDL_STATIC=OFF",
            "-DSDL_TEST=OFF",
            "-DSDL_TESTS=OFF",
            f"-DSDL_VENDOR_INFO={VENDOR}",
        ],
        check=True,
    )
    subprocess.run(
        [
            cmake,
            "--build",
            str(build),
            "--config",
            "Release",
            "--target",
            "SDL2",
            "--parallel",
            "4",
        ],
        check=True,
    )
    shutil.copy2(build / "Release/SDL2.dll", output / "SDL2.dll")
    shutil.copy2(source / "LICENSE.txt", output / "LICENSE.txt")
    manifest = {
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "sdl_version": [2, 28, 4],
        "sdl_revision": REVISION,
        "architecture": "x64",
        "dll_sha256": hashlib.sha256((output / "SDL2.dll").read_bytes()).hexdigest(),
        "patch_sha256": hashlib.sha256(
            (output / "preserve_forces.patch").read_bytes()
        ).hexdigest(),
        "hint": "P8_TSXW_PRESERVE_FORCES",
        "usb_vendor": "044f",
        "usb_product": "b692",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
