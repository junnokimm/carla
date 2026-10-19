"""Compile the real patched open function with inert DirectInput stubs."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile

from tools.build_tsxw_sdl import ROOT, SOURCE_FILE, SOURCE_SHA256, patch_source


def main() -> None:
    work = ROOT / "data/tsxw_sdl_build"
    archive = work / "SDL2-2.28.4.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == SOURCE_SHA256
    with zipfile.ZipFile(archive) as zipped:
        original = (
            zipped.read(f"SDL2-2.28.4/{SOURCE_FILE}").decode().replace("\r\n", "\n")
        )
    actual = (work / "SDL2-2.28.4" / SOURCE_FILE).read_text(encoding="utf-8")
    assert actual == patch_source(original)
    start = actual.index("int SDL_DINPUT_JoystickOpen(")
    end = actual.index("static int SDL_DINPUT_JoystickInitRumble(", start)
    harness = work / "contract"
    harness.mkdir(exist_ok=True)
    (harness / "actual_open.c").write_text(actual[start:end], encoding="utf-8")
    shutil.copy2(ROOT / "tests/native/tsxw_open_mock.c", harness / "tsxw_open_mock.c")
    (harness / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(tsxw_open_contract C)\n"
        "add_executable(tsxw_open_contract tsxw_open_mock.c)\n",
        encoding="utf-8",
    )
    cmake = shutil.which("cmake") or r"C:\Program Files\CMake\bin\cmake.exe"
    build = harness / "build"
    subprocess.run(
        [
            cmake,
            "-S",
            str(harness),
            "-B",
            str(build),
            "-G",
            "Visual Studio 17 2022",
            "-A",
            "x64",
        ],
        check=True,
    )
    subprocess.run([cmake, "--build", str(build), "--config", "Release"], check=True)
    result = subprocess.run(
        [str(build / "Release/tsxw_open_contract.exe")],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    # The input format, sorting, per-frame update, reacquisition and close code
    # outside JoystickOpen must remain byte-for-byte equal (normalizing CRLF).
    old_start = original.index("int SDL_DINPUT_JoystickOpen(")
    old_end = original.index("static int SDL_DINPUT_JoystickInitRumble(", old_start)
    assert actual[:start] == original[:old_start]
    assert actual[end:] == original[old_end:]
    manifest = json.loads((ROOT / "native/tsxw_sdl/manifest.json").read_text())
    assert (
        manifest["dll_sha256"]
        == hashlib.sha256(
            (ROOT / "native/tsxw_sdl/SDL2.dll").read_bytes(),
        ).hexdigest()
    )
    report = {
        "native_open_contract": result.stdout.strip(),
        "outside_open_function_unchanged": True,
        "patched_source_sha256": hashlib.sha256(actual.encode()).hexdigest(),
        "dll_sha256": manifest["dll_sha256"],
        "hardware_opened": False,
    }
    destination = ROOT / "docs/pilot_stability_verification/tsxw_native_contract.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
