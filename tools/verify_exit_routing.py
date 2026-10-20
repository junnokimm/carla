"""Offline native route/indicator regression; never connects to the simulator.

Compile the existing pinned localization patch into a test executable. Do not
relink or overwrite the experiment's Python runtime or any installed package.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from tools.build_noa_tm import BASE, ROOT, PINNED, build_environment, patch_source


def main() -> None:
    work = ROOT / "data/signal_exit_verification/native"
    work.mkdir(parents=True, exist_ok=True)
    for name, digest in PINNED.items():
        if hashlib.sha256((BASE / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Native input hash mismatch: {name}")
    env = build_environment()
    bin_dir = Path(env["VCToolsInstallDir"]) / "bin/Hostx64/x64"
    include = BASE / "install/include"
    source = work / "LocalizationStage.cpp"
    source.write_text(patch_source((include / "carla/trafficmanager/LocalizationStage.cpp").read_text()), encoding="utf8")
    flags = ["/nologo", "/O2", "/MD", "/EHsc", "/std:c++14", "/DNDEBUG",
             "/D_WIN32_WINNT=0x0600", "/DHAVE_SNPRINTF", "/DBOOST_ERROR_CODE_HEADER_ONLY",
             "/DLIBCARLA_IMAGE_WITH_PNG_SUPPORT", f"/I{include}",
             f"/I{BASE / 'source/LibCarla/source/third-party'}"]
    for dep in ("boost-1.84.0-install", "rpclib-install", "recast-install", "libpng-1.2.37-install"):
        flags.append(f"/I{BASE / 'Build' / dep / 'include'}")
    objects = []
    for item in (source, ROOT / "tests/native/noa_tm_localization.cpp"):
        obj = work / (item.stem + ".obj")
        subprocess.run([str(bin_dir / "cl.exe"), *flags, "/c", str(item), f"/Fo{obj}"], env=env, cwd=work, check=True)
        objects.append(str(obj))
    deps = BASE / "source/PythonAPI/carla/dependencies/lib"
    libraries = [str(BASE / "install/lib/carla_client.lib")]
    libraries += [str(deps / name) for name in (
        "Detour.lib", "DetourCrowd.lib", "libboost_filesystem-vc143-mt-x64-1_84.lib",
        "libboost_python312-vc143-mt-x64-1_84.lib", "libpng.lib", "osm2odr.lib", "proj.lib",
        "Recast.lib", "rpc.lib", "sqlite3.lib", "xerces-c_3.lib", "zlib.lib")]
    exe = work / "exit_routing.exe"
    subprocess.run([str(bin_dir / "link.exe"), "/nologo", *objects, *libraries,
                    "shlwapi.lib", "Advapi32.lib", "ole32.lib", "shell32.lib", f"/OUT:{exe}"], env=env, cwd=work, check=True)
    result = subprocess.run([str(exe), r"C:\CarlaUE4\Content\Carla\Maps\OpenDrive\Town04.xodr"],
                            capture_output=True, text=True, cwd=work)
    (work / "result.txt").write_text(result.stdout + result.stderr, encoding="utf8")
    print(result.stdout + result.stderr, flush=True)
    result.check_returncode()


if __name__ == "__main__":
    main()
